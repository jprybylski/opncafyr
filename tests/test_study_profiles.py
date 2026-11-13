"""Study workflow profiles (#68, ADR 0010): format, progress, slots, provenance."""

from __future__ import annotations

import json

import pytest

from opncafyr.core.guidance import build_catalog, shipped_files
from opncafyr.core.manifest import load_schema, validate
from opncafyr.core.registry import load_workflows
from opncafyr.core.workspace import init_workspace
from opncafyr.workflows.nca import study_profile as sp
from publish_helpers import completed_run, picks, writable

CATALOG = build_catalog(load_workflows())
SECRET = "SUBJ-0042"

MINIMAL = """\
schema_version: "1.0"
id: two-period
version: "1"
title: Two periods
steps:
  - id: about
    type: instruction
    title: About
    text: Read me.
  - id: ready
    type: checklist
    title: Ready
    items:
      - id: plan
        text: Plan at hand.
      - id: data
        text: Data imported.
  - id: map
    type: action
    title: Map
    target: nca.mapping.card
    hint_condition: nca.mapping.ready
    text: Map the columns.
  - id: run-a
    type: run
    title: Period A
    slot: a
  - id: run-b
    type: run
    title: Period B
    slot: b
  - id: pub
    type: publication
    title: Both periods
    slots: [a, b]
"""


@pytest.fixture
def study(tmp_path):
    root = tmp_path / "Study"
    root.mkdir()
    init_workspace(root, "0.0.1")
    yield root
    writable(root)


@pytest.fixture
def api(client, study):
    from opncafyr.core.workspace import open_workspace

    client.app.state.workspace = open_workspace(study)
    client.study = study
    client.headers.update(client.session_headers)
    return client


def write_profile(root, text=MINIMAL, name="two-period.yaml"):
    (root / "profiles").mkdir(exist_ok=True)
    (root / "profiles" / name).write_text(text, "utf-8")
    return name


def problems_of(text):
    return sp.examine(text.encode(), "p.yaml", CATALOG)["problems"]


# --- the format ----------------------------------------------------------------------------


def test_shipped_examples_are_valid_and_carry_no_science():
    examples = shipped_files(sp.EXAMPLES)
    assert set(examples) == {"dose-escalation.yaml", "fed-fasted.yaml", "drug-interaction.yaml"}
    for name, data in examples.items():
        found = sp.examine(data, name, CATALOG)
        assert found["problems"] == [], (name, found["problems"])
        kinds = {s["type"] for s in found["profile"]["steps"]}
        assert {"run", "publication", "checkpoint", "action"} <= kinds, name


def test_the_schema_has_no_field_for_a_scientific_choice():
    schema = load_schema("study-profile-v1")
    fields = set(schema["properties"]) | set(schema["$defs"]["step"]["properties"])
    for word in (
        "analyte",
        "dose",
        "unit",
        "blq",
        "exclu",
        "compar",
        "ratio",
        "interval",
        "url",
        "path",
        "command",
        "script",
        "html",
        "endpoint",
        "code",
    ):
        assert not any(p.startswith(word) for f in fields for p in f.lower().split("_")), word
    for scope in (schema, schema["$defs"]["step"]):
        assert scope["additionalProperties"] is False


@pytest.mark.parametrize(
    ("edit", "expected"),
    [
        (lambda t: t.replace("type: instruction", "type: script"), "fails 'enum'"),
        (
            lambda t: t.replace("    text: Read me.\n", "    text: Read me.\n    target: x.y\n"),
            "doesn't take target",
        ),
        (
            lambda t: t.replace("target: nca.mapping.card", "target: nca.mappin.card"),
            "names no target",
        ),
        (
            lambda t: t.replace("hint_condition: nca.mapping.ready", "hint_condition: nca.x.y"),
            "names no condition",
        ),
        (lambda t: t.replace("id: run-b", "id: run-a"), "same id"),
        (lambda t: t.replace("slot: b", "slot: a"), "already used"),
        (lambda t: t.replace("slots: [a, b]", "slots: [a, c]"), "no run step has slot c"),
        (lambda t: t.replace("      - id: data", "      - id: plan"), "same id"),
        (lambda t: t + "    analyte_value: X\n", "additionalProperties"),
        (
            lambda t: t.replace("    title: Map\n", f"    title: Map\n    url: {SECRET}\n"),
            "additionalProperties",
        ),
    ],
)
def test_unsupported_or_wrong_steps_are_explained(edit, expected):
    problems = problems_of(edit(MINIMAL))
    assert problems and any(expected in p for p in problems), problems
    assert not any(SECRET in p for p in problems)


def test_a_publication_step_must_come_after_its_slots():
    moved = (
        MINIMAL.replace("  - id: run-b\n    type: run\n    title: Period B\n    slot: b\n", "")
        + "  - id: run-b\n    type: run\n    title: Period B\n    slot: b\n"
    )
    assert any("comes after" in p for p in problems_of(moved))


# --- choosing and following a profile ---------------------------------------------------------


def test_copy_select_and_resume(api, study):
    listed = api.get("/api/nca/profiles").json()
    assert listed["current"] == {"selected": None} and listed["study"] == []
    assert {e["name"] for e in listed["examples"]} >= {"fed-fasted.yaml"}

    r = api.post("/api/nca/profiles/copy", json={"name": "fed-fasted.yaml"})
    assert r.status_code == 200 and r.json()["path"] == "profiles/fed-fasted.yaml"
    again = api.post("/api/nca/profiles/copy", json={"name": "fed-fasted.yaml"})
    assert again.status_code == 409 and again.json()["detail"]["code"] == "exists"

    current = api.post("/api/nca/profiles/select", json={"name": "fed-fasted.yaml"}).json()
    assert current["selected"] == "fed-fasted.yaml" and current["changed"] is False
    assert not any(s["complete"] for s in current["steps"])

    r = api.put("/api/nca/profiles/current/steps/about", json={"done": True})
    assert r.status_code == 200
    r = api.put("/api/nca/profiles/current/steps/before-you-start", json={"items": ["plan"]})
    step = next(s for s in r.json()["steps"] if s["id"] == "before-you-start")
    assert step["complete"] is False and step["items"] == ["plan"]

    # Progress is on disk, so it survives a reload or a new launch.
    state = json.loads((study / ".opncafyr" / "study-profile.json").read_text("utf-8"))
    validate(state, "study-profile-state-v1")
    resumed = api.get("/api/nca/profiles").json()["current"]
    assert next(s for s in resumed["steps"] if s["id"] == "about")["complete"] is True

    # Choosing none and then the same profile again keeps its progress.
    api.post("/api/nca/profiles/clear")
    assert api.get("/api/nca/profiles").json()["current"] == {"selected": None}
    back = api.post("/api/nca/profiles/select", json={"name": "fed-fasted.yaml"}).json()
    assert next(s for s in back["steps"] if s["id"] == "about")["complete"] is True


def test_an_invalid_profile_cant_be_chosen(api, study):
    write_profile(study, MINIMAL.replace("type: instruction", "type: script"))
    listed = api.get("/api/nca/profiles").json()["study"]
    assert listed[0]["problems"]
    r = api.post("/api/nca/profiles/select", json={"name": "two-period.yaml"})
    assert r.status_code == 422 and r.json()["detail"]["problems"]


def test_names_and_steps_are_checked(api, study):
    write_profile(study)
    api.post("/api/nca/profiles/select", json={"name": "two-period.yaml"})
    for name in ("../x.yaml", ".opncafyr.yaml", "x.yml"):
        assert api.post("/api/nca/profiles/select", json={"name": name}).status_code == 422
    assert api.put("/api/nca/profiles/current/steps/nope", json={"done": True}).status_code == 404
    # A run step is filled with a run, never ticked by hand.
    r = api.put("/api/nca/profiles/current/steps/run-a", json={"done": True})
    assert r.status_code == 422
    r = api.put("/api/nca/profiles/current/steps/ready", json={"items": ["other"]})
    assert r.status_code == 422
    r = api.put("/api/nca/profiles/current/steps/about", json={"items": ["plan"]})
    assert r.status_code == 422


def test_a_writing_request_needs_the_session(client, study):
    from opncafyr.core.workspace import open_workspace

    client.app.state.workspace = open_workspace(study)
    write_profile(study)
    r = client.post("/api/nca/profiles/select", json={"name": "two-period.yaml"})
    assert r.status_code == 403


# --- slots and the publication ----------------------------------------------------------------


def test_slots_take_only_completed_runs_each_once(api, study):
    write_profile(study)
    api.post("/api/nca/profiles/select", json={"name": "two-period.yaml"})
    done, _ = completed_run(study, "Drug A")
    open_run, _ = completed_run(study, "Drug A", state="reviewing")

    r = api.put("/api/nca/profiles/current/slots/a", json={"run_id": open_run})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "run_not_completed"
    r = api.put("/api/nca/profiles/current/slots/zzz", json={"run_id": done})
    assert r.status_code == 404
    r = api.put("/api/nca/profiles/current/slots/a", json={"run_id": "20990101T000000Z-abcd"})
    assert r.status_code == 404

    r = api.put("/api/nca/profiles/current/slots/a", json={"run_id": done})
    assert r.status_code == 200
    assert next(s for s in r.json()["steps"] if s["id"] == "run-a")["complete"] is True
    r = api.put("/api/nca/profiles/current/slots/b", json={"run_id": done})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "run_in_other_slot"

    r = api.put("/api/nca/profiles/current/slots/a", json={"run_id": None})
    assert next(s for s in r.json()["steps"] if s["id"] == "run-a")["complete"] is False


def test_the_publication_must_hold_exactly_the_slots_runs(api, study):
    write_profile(study)
    api.post("/api/nca/profiles/select", json={"name": "two-period.yaml"})
    a, _ = completed_run(study, "Drug A", label="Period A")
    b, _ = completed_run(study, "Drug A", label="Period B")
    other, _ = completed_run(study, "Metabolite B")

    def publish(*run_ids):
        items = [i for r in run_ids for i in picks(r, ["summary"])]
        r = api.post("/api/nca/publications", json={"items": items})
        assert r.status_code == 201, r.text
        return r.json()["publication"]["publication_id"]

    only_a = publish(a)
    both = publish(a, b)
    three = publish(a, b, other)

    r = api.put("/api/nca/profiles/current/publications/pub", json={"publication_id": both})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "slots_empty"
    api.put("/api/nca/profiles/current/slots/a", json={"run_id": a})
    api.put("/api/nca/profiles/current/slots/b", json={"run_id": b})
    for wrong in (only_a, three):
        r = api.put("/api/nca/profiles/current/publications/pub", json={"publication_id": wrong})
        assert r.status_code == 409 and r.json()["detail"]["code"] == "runs_differ"
    r = api.put("/api/nca/profiles/current/publications/run-a", json={"publication_id": both})
    assert r.status_code == 422

    r = api.put("/api/nca/profiles/current/publications/pub", json={"publication_id": both})
    assert r.status_code == 200
    assert next(s for s in r.json()["steps"] if s["id"] == "pub")["complete"] is True
    # Changing a slot afterwards makes the publication step incomplete again.
    r = api.put("/api/nca/profiles/current/slots/b", json={"run_id": None})
    assert next(s for s in r.json()["steps"] if s["id"] == "pub")["complete"] is False

    cands = api.get("/api/nca/profiles/candidates").json()
    assert {x["run_id"] for x in cands["runs"]} == {a, b, other}
    assert sorted(cands["publications"][0]) == sorted(
        ["publication_id", "created_at", "state", "items", "runs"]
    )


def test_publication_records_the_profile_step_only_when_chosen(api, study):
    write_profile(study)
    api.post("/api/nca/profiles/select", json={"name": "two-period.yaml"})
    run_id, _ = completed_run(study, "Drug A")
    plain = api.post("/api/nca/publications", json={"items": picks(run_id, ["summary"])})
    assert "workflow_context" not in plain.json()["publication"]

    r = api.post(
        "/api/nca/publications",
        json={"items": picks(run_id, ["listing"]), "profile_step": "run-a"},
    )
    assert r.status_code == 422 and r.json()["detail"]["code"] == "bad_step"
    # A publication step is recorded only for exactly the runs in the step's slots.
    r = api.post(
        "/api/nca/publications", json={"items": picks(run_id, ["listing"]), "profile_step": "pub"}
    )
    assert r.status_code == 409 and r.json()["detail"]["code"] == "slots_empty"
    other, _ = completed_run(study, "Drug A", label="Period B")
    api.put("/api/nca/profiles/current/slots/a", json={"run_id": run_id})
    api.put("/api/nca/profiles/current/slots/b", json={"run_id": other})
    r = api.post(
        "/api/nca/publications", json={"items": picks(run_id, ["listing"]), "profile_step": "pub"}
    )
    assert r.status_code == 409 and r.json()["detail"]["code"] == "runs_differ"
    assert r.json()["detail"]["missing"] == [other]
    pubs = api.get("/api/nca/publications").json()["publications"]
    assert len(pubs) == 1  # only the plain one: the refused requests recorded nothing
    items = picks(run_id, ["listing"]) + picks(other, ["summary"])
    r = api.post("/api/nca/publications", json={"items": items, "profile_step": "pub"})
    assert r.status_code == 201, r.text
    doc = r.json()["publication"]
    sha = api.get("/api/nca/profiles").json()["current"]["sha256"]
    assert doc["workflow_context"] == {
        "profile_id": "two-period",
        "profile_version": "1",
        "profile_sha256": sha,
        "profile_path": "profiles/two-period.yaml",
        "step_id": "pub",
    }
    validate(doc, "publication-v1")


def test_a_profile_step_needs_a_followed_profile(api, study):
    run_id, _ = completed_run(study, "Drug A")
    r = api.post(
        "/api/nca/publications", json={"items": picks(run_id, ["summary"]), "profile_step": "pub"}
    )
    assert r.status_code == 409 and r.json()["detail"]["code"] == "no_profile"


# --- an edited profile file ------------------------------------------------------------------


def test_an_edited_profile_must_be_adopted_and_keeps_what_still_exists(api, study):
    name = write_profile(study)
    api.post("/api/nca/profiles/select", json={"name": name})
    run_id, _ = completed_run(study, "Drug A")
    api.put("/api/nca/profiles/current/steps/about", json={"done": True})
    api.put("/api/nca/profiles/current/steps/map", json={"done": True})
    api.put("/api/nca/profiles/current/slots/b", json={"run_id": run_id})
    before = api.get("/api/nca/profiles").json()["current"]["sha256"]

    # The team edits the file: "map" is removed, "review" added, slot b is gone.
    edited = (
        MINIMAL.replace('version: "1"', 'version: "2"')
        .replace(
            "  - id: map\n    type: action\n    title: Map\n    target: nca.mapping.card\n"
            "    hint_condition: nca.mapping.ready\n    text: Map the columns.\n",
            "  - id: review\n    type: checkpoint\n    title: Review\n    text: Look again.\n",
        )
        .replace("  - id: run-b\n    type: run\n    title: Period B\n    slot: b\n", "")
        .replace("slots: [a, b]", "slots: [a]")
    )
    write_profile(study, edited, name)
    current = api.get("/api/nca/profiles").json()["current"]
    assert current["changed"] is True and current["adopted_sha256"] == before
    assert current["added"] == ["review"] and current["dropped"] == ["map", "run-b"]
    r = api.put("/api/nca/profiles/current/steps/about", json={"done": False})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "profile_changed"
    r = api.post("/api/nca/profiles/current/adopt", json={"sha256": before})
    assert r.status_code == 409  # not the text on disk now

    adopted = api.post("/api/nca/profiles/current/adopt", json={"sha256": current["sha256"]}).json()
    assert adopted["changed"] is False
    progress = adopted["progress"]
    assert set(progress["steps"]) == {"about"} and progress["slots"] == {}
    assert progress["version"] == "2"
    assert progress["history"] == [
        {"sha256": before, "version": "1", "until": progress["history"][0]["until"]}
    ]


def test_a_missing_profile_file_is_reported(api, study):
    name = write_profile(study)
    api.post("/api/nca/profiles/select", json={"name": name})
    (study / "profiles" / name).unlink()
    current = api.get("/api/nca/profiles").json()["current"]
    assert current["unreadable"] is True and current["code"] == "not_found"
    assert current["selected"] == name
    r = api.put("/api/nca/profiles/current/steps/about", json={"done": True})
    assert r.status_code == 404


def test_a_broken_progress_file_is_never_overwritten(api, study):
    name = write_profile(study)
    path = study / ".opncafyr" / "study-profile.json"
    path.write_text("{not json", "utf-8")
    r = api.post("/api/nca/profiles/select", json={"name": name})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "state_invalid"
    assert path.read_text("utf-8") == "{not json"


def test_run_manifest_schema_takes_a_workflow_context():
    schema = load_schema("run-manifest-v1")
    ctx = schema["properties"]["workflow_context"]
    assert ctx["additionalProperties"] is False
    assert "workflow_context" not in schema["required"]


def test_start_run_passes_the_chosen_step_to_the_run(api, study, monkeypatch):
    # Without R: the checks and the snapshot are stubbed; this checks the wiring only. The
    # real run is in tests/integration/nca/test_runs_r.py.
    from opncafyr.workflows.nca import api as nca_api
    from opncafyr.workflows.nca import checks as checks_mod

    seen = []
    ctx = api.app.state.nca
    monkeypatch.setattr(
        ctx, "checked", lambda recipe_id, sel: {"findings": [], "recipe_sha256": "x"}
    )
    monkeypatch.setattr(checks_mod, "gate", lambda *a: ("ok", []))
    monkeypatch.setattr(
        nca_api, "create_run", lambda *a, **kw: seen.append(kw.get("workflow_context")) or {}
    )
    body = {"recipe_id": "r-0001", "analyte_value": "PARENT"}
    assert api.post("/api/nca/runs", json=body).status_code == 201
    r = api.post("/api/nca/runs", json={**body, "profile_step": "run-a"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "no_profile"

    write_profile(study)
    api.post("/api/nca/profiles/select", json={"name": "two-period.yaml"})
    r = api.post("/api/nca/runs", json={**body, "profile_step": "pub"})
    assert r.status_code == 422  # a publication step is not a run step
    assert api.post("/api/nca/runs", json={**body, "profile_step": "run-a"}).status_code == 201
    assert seen[0] is None
    assert seen[1]["step_id"] == "run-a" and seen[1]["profile_path"] == "profiles/two-period.yaml"
    assert len(seen) == 2


def test_a_progress_file_that_disagrees_with_itself_is_reported(api, study):
    path = study / ".opncafyr" / "study-profile.json"
    path.write_text(json.dumps({"schema_version": "1.0", "selected": "x.yaml", "profiles": {}}))
    listed = api.get("/api/nca/profiles")
    assert listed.status_code == 200  # the page still lists profiles and examples
    assert listed.json()["current"]["error"]["code"] == "state_invalid"
    r = api.put("/api/nca/profiles/current/steps/about", json={"done": True})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "state_invalid"
