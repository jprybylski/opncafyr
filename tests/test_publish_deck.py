"""The deck workflow with a stand-in deckifyr (#33): commands, jobs, editor, builds."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
import yaml

from opncafyr.adapters import deckifyr as D
from opncafyr.core.adapter import ChildActivity
from opncafyr.core.manifest import validate
from opncafyr.core.runtime import Runtime
from opncafyr.core.supervisor import ChildState
from publish_helpers import completed_run, picks, publish_client, wait_job, writable

pytestmark = pytest.mark.skipif(os.name == "nt", reason="the stand-in deckifyr is a shell script")
FAKE = Path(__file__).parent / "fake_deckifyr.py"


def fake_deck_runtime(base: Path) -> Runtime:
    bin_dir = base / "tools" / ".venv" / "bin"
    bin_dir.mkdir(parents=True)
    python = bin_dir / "python"
    python.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{FAKE}" "{base}" "$@"\n', "utf-8")
    python.chmod(0o755)
    other = base / "bin"
    other.mkdir()
    exes = {"deckifyr_python": python}
    for name in ("rscript", "quarto"):
        exes[name] = other / name
        exes[name].write_text("#!/bin/sh\nexit 0\n", "utf-8")
        exes[name].chmod(0o755)
    return Runtime(executables=exes)


class StubSupervisor:
    """Just enough of core.supervisor.Supervisor for the deck routes."""

    def __init__(self) -> None:
        self.tools = ["deckifyr"]
        self.running = False
        self.unsaved = False
        self.sessions: list[dict] = []

    def state(self, tool):
        return ChildState.RUNNING if self.running else ChildState.NOT_STARTED

    def activity(self):
        return {"deckifyr": ChildActivity(unsaved=self.unsaved, busy=False)} if self.running else {}

    def start(self, tool, session):
        self.sessions.append(dict(session))
        self.running = True

    def stop(self, tool):
        self.unsaved = False
        self.running = False


@pytest.fixture
def api(security, tmp_path):
    study = (tmp_path / "Study").resolve()
    study.mkdir()
    rt = fake_deck_runtime(tmp_path / "fake")
    with publish_client(security, tmp_path, study, rt) as client:
        client.fake = tmp_path / "fake"
        client.app.state.supervisor = client.sup = StubSupervisor()
        yield client
    writable(study)


def mode(api, step: str, value: str) -> None:
    (api.fake / f"{step}.txt").write_text(value, "utf-8")


def publish(api, *runs, artifacts=("summary", "profiles-linear")):
    items = [p for run in runs for p in picks(run, artifacts)]
    r = api.post("/api/nca/publications", json={"items": items})
    assert r.status_code == 201, r.text
    return r.json()["publication"]


@pytest.fixture
def ready(api):
    a, _ = completed_run(api.study, "Drug A")
    b, _ = completed_run(api.study, "Metabolite B")
    pub = publish(api, a, b)
    out = api.post("/api/nca/deck", json={"publication_id": pub["publication_id"]})
    assert out.status_code == 200, out.text
    job = wait_job(api, out.json()["job_id"], 60)
    assert job["state"] == "completed", job["message"]
    return pub, a, b


def build(api, previews=False):
    r = api.post("/api/nca/deck/build", json={"previews": previews})
    assert r.status_code == 200, r.text
    out = r.json()
    return out, wait_job(api, out["job_id"], 60)


# --- adapter -------------------------------------------------------------------------------


def test_cli_commands_are_offline_and_trusted_only(tmp_path):
    rt = fake_deck_runtime(tmp_path)
    spec = D.build_command(rt, tmp_path / "deck" / "presentation.yaml")
    py = str(rt.executable("deckifyr_python"))
    assert spec.argv[:7] == [
        py,
        "-I",
        "-m",
        "deckifyr",
        "--json",
        "build",
        str(tmp_path / "deck" / "presentation.yaml"),
    ]
    assert "--strict" in spec.argv and "--trusted-binaries-only" in spec.argv
    assert f"--rscript-bin={rt.executable('rscript')}" in spec.argv
    assert f"--quarto-bin={rt.executable('quarto')}" in spec.argv
    assert not any(a.startswith("--soffice-bin") for a in spec.argv)  # not installed here
    assert spec.env["DECKIFYR_OFFLINE"] == "1" and spec.env["UV_OFFLINE"] == "1"
    init = D.init_command(rt, tmp_path / "x")
    assert init.argv[5:] == ["init", str(tmp_path / "x"), "--force"]
    assert "--from-repo" not in init.argv


def test_parse_cli_judges_exit_code_and_json(tmp_path):
    good = json.dumps({"status": "ok", "result_version": 1, "warnings": [{"code": "W_X"}]})
    assert D.parse_cli(0, good, "").ok and D.parse_cli(0, good, "").warnings == [{"code": "W_X"}]
    assert not D.parse_cli(0, "", "").ok
    assert not D.parse_cli(0, json.dumps({"status": "ok", "result_version": 2}), "").ok
    err = json.dumps(
        {
            "status": "error",
            "result_version": 1,
            "code": "E_PATH_OUTSIDE_PROJECT",
            "message": f"{tmp_path}/deck/a.png is outside /Users/someone/Study",
        }
    )
    out = D.parse_cli(1, "", "Traceback noise\n" + err, tmp_path / "deck")
    assert not out.ok and "outside its folder" in out.message
    assert "/Users/someone" not in out.message and str(tmp_path) not in out.message
    assert "exit code 2" in D.parse_cli(2, "", "usage: deckifyr").message
    assert "exit code 143" in D.parse_cli(143, "", "").message


def test_the_generated_deck_quotes_data_and_has_no_bom():
    evil = 'Drug "A": {{< x >}}\n- injected: true'
    items = [
        D.DeckItem("a_b.png", "figure", "Profiles", evil, "20261103T142512Z-7c4e"),
        D.DeckItem("a_b.csv", "table", "Summary", "B", "20261103T142512Z-7c4e"),
    ]
    text = D.nca_results_presentation(items, "pub-20261104T090000Z-1a2b", None)
    assert not text.encode("utf-8").startswith(b"\xef\xbb\xbf")
    doc = yaml.safe_load(text)
    assert doc["build"]["manifest"] and doc["build"]["reportifyr"]["fail_on_missing_metadata"]
    slides = doc["slides"]
    assert [s["layout"] for s in slides] == ["blank", "title-content", "title-content"]
    assert slides[1]["elements"]["title"]["value"] == f"Profiles: {evil}"
    assert slides[1]["elements"]["content"] == {
        "type": "reportifyr",
        "value": "{rpfy}:a_b.png",
        "fit": "contain",
        "alt_text": f"Profiles for {evil}, run 20261103T142512Z-7c4e.",
    }
    assert slides[2]["elements"]["content"]["type"] == "table"
    assert "injected" not in doc


def test_a_formatted_table_is_a_picture_slide_and_a_csv_table_a_native_one():
    """#62: deckifyr renders a flextable ``.rds`` only as a ``reportifyr`` element (a picture).

    Under ``type: table`` it raises E_CONTENT_VALIDATION ("use a 'type: reportifyr'
    element instead", deckifyr 0.3.6), so a deck with a formatted table could not build."""
    items = [
        D.DeckItem("a_b_summary-formatted.rds", "table", "Summary", "A", "20261103T142512Z-7c4e"),
        D.DeckItem("a_b_summary.csv", "table", "Summary", "A", "20261103T142512Z-7c4e"),
    ]
    doc = yaml.safe_load(D.nca_results_presentation(items, "pub-20261104T090000Z-1a2b", None))
    formatted = doc["slides"][1]["elements"]["content"]
    assert formatted["type"] == "reportifyr"
    assert formatted["value"] == "{rpfy}:a_b_summary-formatted.rds"
    assert "source" not in formatted
    native = doc["slides"][2]["elements"]["content"]
    assert native["type"] == "table" and native["source"] == "{rpfy}:a_b_summary.csv"


def test_an_unexpected_deckifyr_error_is_readable_and_names_the_exception():
    err = json.dumps(
        {"status": "error", "code": "E_INTERNAL", "message": "IndexError: tuple index out of range"}
    )
    out = D.parse_cli(1, "", err)
    assert not out.ok
    assert "unexpected error in the deck tool" in out.message
    assert "IndexError: tuple index out of range" in out.message


# --- the workflow --------------------------------------------------------------------------


def test_setup_writes_the_nca_results_deck(api, ready):
    pub, _, _ = ready
    deck = api.study / "deck"
    for name in ("design.yaml", "layouts.yaml", "presentation.yaml", "standard_footnotes.yaml"):
        assert (deck / name).is_file(), name
    doc = yaml.safe_load((deck / "presentation.yaml").read_text("utf-8"))
    assert len(doc["slides"]) == 1 + len(pub["items"])
    copies = {p.relative_to(deck).as_posix() for p in (deck / "OUTPUTS").rglob("*") if p.is_file()}
    assert copies == {p for i in pub["items"] for p in (i["published_path"], i["sidecar_path"])}
    status = api.get("/api/nca/deck").json()
    assert status["binding"]["publication_id"] == pub["publication_id"] and status["edited"] == []


def test_check_lists_launcher_and_deckifyr_findings(api, ready):
    mode(api, "validate", "warn")
    job = wait_job(api, api.post("/api/nca/deck/check").json()["job_id"], 60)
    assert job["state"] == "completed", job["message"]
    report = api.get("/api/nca/deck").json()["check"]
    validate(report, "preflight-v1")
    [found] = report["findings"]
    assert found["check"] == "w-unitless-length" and found["severity"] == "warning"
    assert "s01-table" in found["message"] and "/" + "private" not in found["message"]


@pytest.mark.parametrize("value", ["fail", "usage"])
def test_a_validation_failure_is_a_readable_job_failure(api, ready, value):
    mode(api, "validate", value)
    job = wait_job(api, api.post("/api/nca/deck/check").json()["job_id"], 60)
    assert job["state"] == "failed"
    assert job["message"].startswith("The deck did not validate.")
    if value == "fail":
        assert "A slide's content can't be used" in job["message"]
        assert str(api.study) not in job["message"]


def test_build_keeps_the_pptx_its_manifest_and_listed_previews(api, ready):
    pub, a, b = ready
    api.app.state.deck.preview = (True, "probe passed")  # no LibreOffice in unit tests
    out, job = build(api, previews=True)
    assert job["state"] == "completed", job["message"]
    doc = api.get("/api/nca/deck").json()["builds"][0]
    validate(doc, "publish-build-v1")
    assert doc["build_id"] == out["build_id"] and sorted(doc["runs"]) == sorted([a, b])
    assert [i["sha256"] for i in doc["items"]] == [i["published_sha256"] for i in pub["items"]]
    assert doc["tools"]["deckifyr"] == "0.3.6" and doc["settings"] == {"previews": True}
    assert {o["role"] for o in doc["outputs"]} == {"pptx", "deck_manifest", "preview"}
    folder = api.study / "deck" / "builds" / out["build_id"]
    assert sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file()) == [
        "build-result.json",
        "build.json",
        "deck-manifest.json",
        "nca-results.pptx",
        "previews/nca-results-01.png",
        "previews/nca-results-02.png",
    ]  # not the stale one
    assert (
        doc["warnings"][0]["code"] == "W_GENERAL"
        and str(api.study) not in doc["warnings"][0]["message"]
    )
    seen = json.loads((api.fake / "seen.json").read_text("utf-8"))
    assert set(seen["files"]) == {
        p for i in pub["items"] for p in (i["published_path"], i["sidecar_path"])
    }
    assert "/." not in seen["cwd"]
    roles = {i["role"] for i in doc["inputs"]}
    assert {"presentation", "project_file", "sidecar", "table", "figure"} <= roles
    r = api.get(f"/api/nca/deck/builds/{out['build_id']}/pptx")
    assert r.status_code == 200 and r.content == b"PK fake pptx"
    (folder / "nca-results.pptx").write_bytes(b"tampered")
    assert api.get(f"/api/nca/deck/builds/{out['build_id']}/pptx").status_code == 409
    # The user's presentation.yaml was not changed to turn previews on.
    assert (
        yaml.safe_load((api.study / "deck" / "presentation.yaml").read_text("utf-8"))["build"][
            "previews"
        ]
        is False
    )


@pytest.mark.parametrize(
    ("value", "words"),
    [
        ("badsha", "doesn't match the hash"),
        ("nomanifest", "names no build manifest"),
        ("garbage", "without a result"),
        ("fail", "names a program of its own"),
    ],
)
def test_a_failed_build_leaves_nothing(api, ready, value, words):
    mode(api, "build", value)
    out, job = build(api)
    assert job["state"] == "failed" and words in job["message"], job["message"]
    assert api.get("/api/nca/deck").json()["builds"] == []
    assert not (api.study / "deck" / "builds" / out["build_id"]).exists()


def test_previews_need_a_passing_probe(api, ready):
    r = api.post("/api/nca/deck/build", json={"previews": True})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "previews_unavailable"
    assert api.post("/api/nca/deck/previews").json() == {
        "available": False,
        "message": "LibreOffice is not installed.",
    }


def test_build_needs_a_manifest_setting(api, ready):
    path = api.study / "deck" / "presentation.yaml"
    doc = yaml.safe_load(path.read_text("utf-8"))
    del doc["build"]["manifest"]
    path.write_text(yaml.safe_dump(doc), "utf-8")
    _, job = build(api)
    assert job["state"] == "failed" and "build.manifest" in job["message"]


def test_unsaved_editor_edits_block_checks_builds_and_rebinding(api, ready):
    pub, a, _ = ready
    r = api.post("/api/nca/deck/editor")
    assert r.status_code == 200 and r.json()["route"] == "/apps/deckifyr/"
    [session] = api.sup.sessions
    assert (
        session["project"] == str(api.study / "deck")
        and session["presentation"] == "presentation.yaml"
    )
    assert session["job_timeout"] < 1200  # below the launcher's own build limit
    api.sup.unsaved = True
    for path, body in (("/api/nca/deck/check", None), ("/api/nca/deck/build", {"previews": False})):
        r = api.post(path, json=body)
        assert r.status_code == 409 and r.json()["detail"]["code"] == "editor_unsaved"
    other = publish(api, a, artifacts=("listing",))
    r = api.post("/api/nca/deck", json={"publication_id": other["publication_id"]})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "editor_open"
    assert api.delete("/api/nca/deck/editor").json() == {"editor": "stopped"}
    assert not api.sup.running
    _, job = build(api)
    assert job["state"] == "completed", job["message"]


def test_opening_the_editor_restores_the_outputs_copy(api, ready):
    pub, _, _ = ready
    copy = api.study / "deck" / pub["items"][0]["published_path"]
    copy.write_text("edited", "utf-8")
    (api.study / "deck" / "OUTPUTS" / "tables" / "stray.csv").write_text("x", "utf-8")
    assert api.post("/api/nca/deck/editor").status_code == 200
    assert copy.read_bytes() == (api.study / pub["items"][0]["published_path"]).read_bytes()
    assert not (api.study / "deck" / "OUTPUTS" / "tables" / "stray.csv").exists()


def test_rebinding_an_edited_deck_asks_first(api, ready):
    pub, a, _ = ready
    path = api.study / "deck" / "presentation.yaml"
    path.write_text(path.read_text("utf-8") + "# my note\n", "utf-8")
    other = publish(api, a, artifacts=("listing",))
    body = {"publication_id": other["publication_id"]}
    r = api.post("/api/nca/deck", json=body)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "needs_choice"
    assert api.post("/api/nca/deck", json={**body, "source": "replace"}).status_code == 200
    doc = yaml.safe_load(path.read_text("utf-8"))
    assert len(doc["slides"]) == 2 and "# my note" not in path.read_text("utf-8")


# --- Quarto fragments in the launcher's editor (#52) ---------------------------------------

FRAGMENTS = "/api/tools/deckifyr/project"


def new_fragment(api, name):
    r = api.post(f"{FRAGMENTS}/create", json={"rule": 0, "name": name})
    assert r.status_code == 200, r.text
    return r.json()


def test_fragments_can_be_made_and_edited_but_nothing_else(api, ready):
    project = api.study / "deck"
    doc = api.get(FRAGMENTS).json()
    assert doc["title"] == "Deck fragments" and doc["files"] == []
    assert doc["new_files"] == [
        {"rule": 0, "folder": "fragments", "suffix": ".qmd", "label": "Quarto fragment"}
    ]
    made = new_fragment(api, "interpretation")
    assert made["path"] == "fragments/interpretation.qmd" and made["syntax"] == "qmd"
    assert (project / "fragments" / "interpretation.qmd").read_text("utf-8") == made["text"]
    # Fragments elsewhere in the project are offered; output, copies and the deck are not.
    for rel in ("notes.qmd", "build/x.qmd", "builds/b/x.qmd", "OUTPUTS/x.qmd", ".hid/x.qmd"):
        (project / rel).parent.mkdir(parents=True, exist_ok=True)
        (project / rel).write_bytes(b"x\n")
    listed = [f["path"] for f in api.get(FRAGMENTS).json()["files"]]
    assert listed == ["notes.qmd", "fragments/interpretation.qmd"]
    for rel in ("presentation.yaml", "build/x.qmd", "OUTPUTS/x.qmd"):
        assert api.post(f"{FRAGMENTS}/read", json={"path": rel}).status_code == 403, rel


def test_saving_a_fragment_warns_about_deckifyrs_limits(api, ready):
    made = new_fragment(api, "two")
    save = lambda text, sha: api.post(  # noqa: E731
        f"{FRAGMENTS}/save",
        json={"path": made["path"], "text": text, "expected_sha256": sha},
    ).json()
    fine = "---\ntitle: x\n---\n\n# One\n\n```{r}\n# a comment\n---\n```\n"
    out = save(fine, made["sha256"])
    assert out["warnings"] == []
    out = save("# One\n\ntext\n\n---\n\n# Two\n", out["sha256"])
    assert len(out["warnings"]) == 2
    assert "slide break" in out["warnings"][0] and "heading" in out["warnings"][1]


def test_unsaved_fragment_text_blocks_check_build_and_rebinding(api, ready):
    pub, a, _ = ready
    api.post("/api/session/heartbeat", json={"page_id": "p1", "unsaved": ["deckifyr"]})
    other = publish(api, a, artifacts=("listing",))
    for path, body in (
        ("/api/nca/deck/check", None),
        ("/api/nca/deck/build", {"previews": False}),
        ("/api/nca/deck", {"publication_id": other["publication_id"]}),
    ):
        r = api.post(path, json=body)
        assert r.status_code == 409 and r.json()["detail"]["code"] == "editor_unsaved", path
    api.post("/api/session/heartbeat", json={"page_id": "p1", "unsaved": []})
    assert build(api)[1]["state"] == "completed"


def test_a_build_copies_the_deck_under_the_editor_lock(api, ready, monkeypatch):
    from opncafyr.workflows.nca.publish import projects

    held = []
    real = projects.copy_project

    def watched(project, dest, skip):
        held.append(api.app.state.project_files.lock(api.study, "deckifyr").locked())
        real(project, dest, skip)

    monkeypatch.setattr(projects, "copy_project", watched)
    new_fragment(api, "kept")
    _, job = build(api)
    assert job["state"] == "completed", job["message"]
    assert held == [True]
