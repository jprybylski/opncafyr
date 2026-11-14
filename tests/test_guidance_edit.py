"""The in-app editor for study profiles and tutorials (#76, ADR 0010).

The page sends a document; the server writes the YAML, checks those exact bytes like any
listing and writes one file in profiles/ or tutorials/, never replacing a file that changed
or that it didn't open. Problems name the step and field, never the value.
"""

from __future__ import annotations

import os
import threading

import pytest

from opncafyr.core import guidance_edit as ge
from opncafyr.core import tutorials as tu
from opncafyr.core.guidance import (
    MAX_GUIDANCE_FILES,
    build_catalog,
    parse_guidance,
    sha256_bytes,
    shipped_files,
)
from opncafyr.core.manifest import load_schema
from opncafyr.core.registry import load_workflows
from opncafyr.core.workspace import init_workspace, open_workspace
from opncafyr.workflows.nca import study_profile as sp
from publish_helpers import writable

CATALOG = build_catalog(load_workflows())
SECRET = "zz-secret.value"
TUTORIAL_EXAMPLES = ("opncafyr.workflows.nca", "tutorials")


@pytest.fixture
def study(tmp_path):
    root = tmp_path / "Study"
    root.mkdir()
    init_workspace(root, "0.0.1")
    yield root
    writable(root)


@pytest.fixture
def api(client, study):
    client.app.state.workspace = open_workspace(study)
    client.study = study
    client.headers.update(client.session_headers)
    return client


def example_doc(source, schema, name):
    doc, problems = parse_guidance(shipped_files(source)[name], schema, name)
    assert problems == []
    return doc


def profile_doc():
    return example_doc(sp.EXAMPLES, sp.SCHEMA, "fed-fasted.yaml")


def tutorial_doc():
    return example_doc(TUTORIAL_EXAMPLES, tu.SCHEMA, "first-analysis.yaml")


# --- the file text -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("source", "schema", "spec"),
    [(sp.EXAMPLES, sp.SCHEMA, sp.FORM), (TUTORIAL_EXAMPLES, tu.SCHEMA, tu.FORM)],
)
def test_every_shipped_example_round_trips(source, schema, spec):
    for name, data in shipped_files(source).items():
        doc, _ = parse_guidance(data, schema, name)
        text = ge.dump_guidance(doc, spec)
        assert parse_guidance(text, schema, name) == (doc, [])
        assert text.decode().startswith("schema_version: '1.0'\nid: ")


def test_awkward_text_reads_back_exactly():
    doc = tutorial_doc()
    awkward = [
        "yes",
        "1.0",
        "null",
        "- dash",
        "key: value",
        "# not a comment",
        "two\nlines\n",
        "trailing space \nnext",
        "quotes ' and \" and émoji ✓",
        "@at",
    ]
    for i, text in enumerate(awkward):
        doc["steps"][i % len(doc["steps"])]["text"] = text
        out = ge.dump_guidance(doc, tu.FORM)
        back, problems = parse_guidance(out, tu.SCHEMA, "t")
        assert problems == [] and back == doc, text


def test_multi_line_text_is_a_block():
    doc = tutorial_doc()
    doc["steps"][0]["text"] = "First line.\nSecond line."
    assert (
        "text: |-\n      First line.\n      Second line." in ge.dump_guidance(doc, tu.FORM).decode()
    )


def test_comment_lines_and_locate():
    assert ge.comment_lines(b"# a\nid: x\n  # b\ntext: 'not # this'\n") == 2
    assert ge.locate(
        [
            "steps/3 (map)/target names no target this app has",
            "profiles/p.yaml at /steps/12/items/0 fails 'required'",
            "This profile at /title fails 'minLength'",
        ]
    ) == [
        {"step": 3, "text": "steps/3 (map)/target names no target this app has"},
        {"step": 12, "text": "profiles/p.yaml at /steps/12/items/0 fails 'required'"},
        {"step": None, "text": "This profile at /title fails 'minLength'"},
    ]


# --- the form specs stay in step with the schemas --------------------------------------


def _names(fields):
    return [f["name"] for f in fields]


def test_the_profile_form_covers_the_schema_and_the_step_types():
    schema = load_schema(sp.SCHEMA)
    step = schema["$defs"]["step"]
    assert set(_names(sp.FORM["document"])) == set(schema["properties"]) - {
        "schema_version",
        "steps",
    }
    assert set(_names(sp.FORM["step"])) == sp.COMMON
    types = {t["id"]: t for t in sp.FORM["step_types"]}
    assert set(types) == set(sp.FIELDS) == set(step["properties"]["type"]["enum"])
    required = {
        rule["if"]["properties"]["type"]["const"]: set(rule["then"]["required"])
        for rule in step["allOf"]
    }
    for name, t in types.items():
        assert set(_names(t["fields"])) == sp.FIELDS[name], name
        assert {f["name"] for f in t["fields"] if f["required"]} == required[name], name
    doc_required = set(schema["required"]) - {"schema_version", "steps"}
    assert {f["name"] for f in sp.FORM["document"] if f["required"]} == doc_required


def test_the_tutorial_form_covers_the_schema():
    schema = load_schema(tu.SCHEMA)
    step = schema["properties"]["steps"]["items"]
    assert set(_names(tu.FORM["document"])) == set(schema["properties"]) - {
        "schema_version",
        "steps",
    }
    assert set(_names(tu.FORM["step"])) == set(step["properties"])
    assert {f["name"] for f in tu.FORM["step"] if f["required"]} == set(step["required"])
    assert tu.FORM["step_types"] is None


# --- saving ---------------------------------------------------------------------------------


def test_save_creates_only_and_then_replaces_only_the_opened_revision(study):
    lock = threading.Lock()
    save = ge.save_study_file
    sha = save(study, "profiles", "a.yaml", b"one\n", expected_sha256=None, lock=lock)
    assert (study / "profiles" / "a.yaml").read_bytes() == b"one\n"
    with pytest.raises(ge.GuidanceWriteError) as exc:
        save(study, "profiles", "a.yaml", b"two\n", expected_sha256=None, lock=lock)
    assert exc.value.code == "exists"
    save(study, "profiles", "a.yaml", b"two\n", expected_sha256=sha, lock=lock)
    with pytest.raises(ge.GuidanceWriteError) as exc:
        save(study, "profiles", "a.yaml", b"three\n", expected_sha256=sha, lock=lock)
    assert exc.value.code == "conflict"
    assert exc.value.extra["current_sha256"] == sha256_bytes(b"two\n")
    assert (study / "profiles" / "a.yaml").read_bytes() == b"two\n"
    with pytest.raises(ge.GuidanceWriteError) as exc:
        save(study, "profiles", "gone.yaml", b"x", expected_sha256=sha, lock=lock)
    assert exc.value.code == "not_found"


@pytest.mark.parametrize("name", ["../x.yaml", "sub/x.yaml", ".x.yaml", "X.yaml", "x.yml", ""])
def test_save_refuses_names_that_are_not_a_plain_file(study, name):
    with pytest.raises(ge.GuidanceWriteError) as exc:
        ge.save_study_file(
            study, "profiles", name, b"x", expected_sha256=None, lock=threading.Lock()
        )
    assert exc.value.code == "invalid_name"


@pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
def test_save_never_follows_a_link_out_of_the_folder(study, tmp_path):
    (study / "profiles").mkdir()
    (study / "results").mkdir()
    (study / "profiles" / "inside.yaml").symlink_to(study / "results" / "x.yaml")
    outside = tmp_path / "outside"
    outside.mkdir()
    (study / "tutorials").symlink_to(outside, target_is_directory=True)
    lock = threading.Lock()
    for folder, name in (("profiles", "inside.yaml"), ("tutorials", "x.yaml")):
        with pytest.raises(ge.GuidanceWriteError) as exc:
            ge.save_study_file(study, folder, name, b"x", expected_sha256=None, lock=lock)
        assert exc.value.code == "invalid_name"
    assert not (study / "results" / "x.yaml").exists()
    assert list(outside.iterdir()) == []


def test_save_caps_size_and_count(study):
    lock = threading.Lock()
    with pytest.raises(ge.GuidanceWriteError) as exc:
        ge.save_study_file(
            study, "tutorials", "big.yaml", b"x" * 300_000, expected_sha256=None, lock=lock
        )
    assert exc.value.code == "too_large"
    (study / "tutorials").mkdir()
    for n in range(MAX_GUIDANCE_FILES):
        (study / "tutorials" / f"t{n}.yaml").write_text("x")
    with pytest.raises(ge.GuidanceWriteError) as exc:
        ge.save_study_file(
            study, "tutorials", "one-more.yaml", b"x", expected_sha256=None, lock=lock
        )
    assert exc.value.code == "too_many"


# --- the routes -----------------------------------------------------------------------------

KINDS = [
    ("/api/nca/profiles", "profiles", "fed-fasted.yaml", profile_doc),
    ("/api/tutorials", "tutorials", "nca/first-analysis.yaml", tutorial_doc),
]


@pytest.mark.parametrize(("prefix", "folder", "example", "make"), KINDS)
def test_new_from_example_then_edit(api, study, prefix, folder, example, make):
    spec = api.get(f"{prefix}/form").json()
    assert spec["folder"] == folder and spec["spec"]["step"]
    opened = api.get(f"{prefix}/edit", params={"example": example}).json()
    assert opened["new"] is True and opened["sha256"] is None
    assert opened["doc"] == make() and opened["problems"] == []
    assert opened["comment_lines"] > 0

    doc = opened["doc"]
    doc["title"] = "Our version"
    doc["steps"].append({**doc["steps"][0], "id": "again"})
    assert api.post(f"{prefix}/check", json={"doc": doc}).json() == {"problems": []}
    r = api.put(f"{prefix}/edit", json={"name": "ours.yaml", "doc": doc})
    assert r.status_code == 200, r.text
    saved = r.json()
    assert saved["path"] == f"{folder}/ours.yaml"
    data = (study / folder / "ours.yaml").read_bytes()
    assert saved["sha256"] == sha256_bytes(data)

    # The listing reads the same file; opening it gives back the document.
    again = api.get(f"{prefix}/edit", params={"name": "ours.yaml"}).json()
    assert again["doc"] == doc and again["sha256"] == saved["sha256"]
    assert again["comment_lines"] == 0 and again["new"] is False

    # A second "new" file of that name is refused; an edit of the opened revision works.
    r = api.put(f"{prefix}/edit", json={"name": "ours.yaml", "doc": doc})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "exists"
    doc["version"] = "2"
    r = api.put(
        f"{prefix}/edit",
        json={"name": "ours.yaml", "doc": doc, "expected_sha256": saved["sha256"]},
    )
    assert r.status_code == 200
    # Saving against the old revision again is a conflict, and nothing changes.
    before = (study / folder / "ours.yaml").read_bytes()
    r = api.put(
        f"{prefix}/edit",
        json={"name": "ours.yaml", "doc": doc, "expected_sha256": saved["sha256"]},
    )
    assert r.status_code == 409 and r.json()["detail"]["code"] == "conflict"
    assert (study / folder / "ours.yaml").read_bytes() == before


@pytest.mark.parametrize(("prefix", "folder", "example", "make"), KINDS)
def test_unknown_values_are_refused_by_place_never_by_value(
    api, study, prefix, folder, example, make
):
    # A catalog id this app lacks (checked once the file is well formed) ...
    doc = make()
    n = next(i for i, s in enumerate(doc["steps"]) if "target" in s)
    doc["steps"][n]["target"] = SECRET
    # ... and a schema rule (checked first).
    bad_title = make()
    bad_title["steps"][2]["title"] = SECRET + "\nsecond line"
    for d, step, field in ((doc, n, "target"), (bad_title, 2, "title")):
        for r in (
            api.post(f"{prefix}/check", json={"doc": d}),
            api.put(f"{prefix}/edit", json={"name": "bad.yaml", "doc": d}),
        ):
            assert SECRET not in r.text
        problems = api.post(f"{prefix}/check", json={"doc": d}).json()["problems"]
        assert [p["step"] for p in problems] == [step], problems
        assert field in problems[0]["text"]
        r = api.put(f"{prefix}/edit", json={"name": "bad.yaml", "doc": d})
        assert r.status_code == 422 and r.json()["detail"]["code"] == "invalid"
        assert r.json()["detail"]["problems"]
    assert not (study / folder / "bad.yaml").exists()


@pytest.mark.parametrize(("prefix", "folder", "example", "make"), KINDS)
def test_only_text_is_accepted(api, study, prefix, folder, example, make):
    doc = make()
    doc["steps"][0]["title"] = {"nested": [1], SECRET: 2}
    doc["version"] = 3
    r = api.post(f"{prefix}/check", json={"doc": doc})
    assert SECRET not in r.text
    problems = r.json()["problems"]
    assert sorted(p["step"] if p["step"] is not None else -1 for p in problems) == [-1, 0, 0]
    deep = {"a": {"b": {"c": {"d": {"e": {"f": {"g": "x"}}}}}}}
    assert api.post(f"{prefix}/check", json={"doc": deep}).json()["problems"]


@pytest.mark.parametrize(("prefix", "folder", "example", "make"), KINDS)
def test_bad_requests(api, client, study, prefix, folder, example, make):
    assert api.get(f"{prefix}/edit").status_code == 404
    assert api.get(f"{prefix}/edit", params={"example": "nope.yaml"}).status_code == 404
    assert api.get(f"{prefix}/edit", params={"name": "nope.yaml"}).status_code == 404
    assert api.get(f"{prefix}/edit", params={"name": "../x.yaml"}).status_code == 422
    for name in ("../x.yaml", "x.yml", "sub/x.yaml"):
        r = api.put(f"{prefix}/edit", json={"name": name, "doc": make()})
        assert r.status_code == 422
    # Writing needs the session; checking too (it is a POST), reading doesn't.
    del api.headers["x-opncafyr-session"]
    assert api.put(f"{prefix}/edit", json={"name": "x.yaml", "doc": make()}).status_code == 403
    assert api.post(f"{prefix}/check", json={"doc": make()}).status_code == 403
    assert not (study / folder / "x.yaml").exists()


@pytest.mark.parametrize(("prefix", "folder", "example", "make"), KINDS)
def test_no_study_open(client, prefix, folder, example, make):
    client.headers.update(client.session_headers)
    r = client.put(f"{prefix}/edit", json={"name": "x.yaml", "doc": make()})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "no_workspace"
    assert client.get(f"{prefix}/edit", params={"example": example}).status_code == 200


@pytest.mark.parametrize(("prefix", "folder", "example", "make"), KINDS)
def test_a_busy_workspace_waits(api, study, prefix, folder, example, make):
    class Lease:
        holder = "deckifyr"

    api.app.state.workspace_lease = Lease()
    r = api.put(f"{prefix}/edit", json={"name": "x.yaml", "doc": make()})
    assert r.status_code == 423 and r.json()["detail"]["code"] == "workspace_busy"


def test_a_file_that_fails_the_schema_opens_with_its_problems_only(api, study):
    (study / "tutorials").mkdir()
    (study / "tutorials" / "broken.yaml").write_text("id: [unclosed\n", "utf-8")
    opened = api.get("/api/tutorials/edit", params={"name": "broken.yaml"}).json()
    assert opened["doc"] is None and opened["problems"][0]["text"].startswith("tutorials/")


def test_editing_the_followed_profile_asks_to_adopt_it(api, study):
    r = api.put("/api/nca/profiles/edit", json={"name": "ff.yaml", "doc": profile_doc()})
    sha = r.json()["sha256"]
    api.post("/api/nca/profiles/select", json={"name": "ff.yaml"}).raise_for_status()
    state_before = (study / sp.STATE_FILE).read_bytes()

    doc = profile_doc()
    doc["steps"].insert(1, {"id": "extra", "type": "instruction", "title": "More", "text": "Hi."})
    r = api.put(
        "/api/nca/profiles/edit", json={"name": "ff.yaml", "doc": doc, "expected_sha256": sha}
    )
    assert r.status_code == 200
    # Saving changed only the file: progress is untouched until the user adopts.
    assert (study / sp.STATE_FILE).read_bytes() == state_before
    current = api.get("/api/nca/profiles").json()["current"]
    assert current["changed"] is True and current["added"] == ["extra"]
    api.post(
        "/api/nca/profiles/current/adopt", json={"sha256": r.json()["sha256"]}
    ).raise_for_status()
    assert api.get("/api/nca/profiles").json()["current"]["changed"] is False
