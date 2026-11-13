"""Report and deck setup from imported standards (#70, ADR 0011), with stand-in tools."""

from __future__ import annotations

import json
import os

import pytest
import yaml

from opncafyr.adapters import deckifyr as D
from opncafyr.adapters import quartifyr as Q
from opncafyr.core.manifest import validate
from opncafyr.core.runtime import Runtime
from publish_helpers import completed_run, picks, publish_client, wait_job, writable
from standards_helpers import CONFIG, DESIGN, FOOTNOTES, LOGO, deck_files, mixed_bundle, sha
from test_publish_deck import StubSupervisor, fake_deck_runtime
from test_publish_report import fake_runtime

pytestmark = pytest.mark.skipif(os.name == "nt", reason="the stand-in tools are shell scripts")

# A footnotes file with one extra abbreviation, so the selected one is recognisable.
HOUSE_FOOTNOTES = FOOTNOTES.replace(
    b"abbreviations:\n", b"abbreviations:\n HOUSE: House style\n", 1
)


@pytest.fixture
def api(security, tmp_path, monkeypatch):
    monkeypatch.setenv("OPNCAFYR_APP_ROOT", str(tmp_path / "app"))
    study = (tmp_path / "Study").resolve()
    study.mkdir()
    report_rt = fake_runtime(tmp_path / "fake")
    deck_rt = fake_deck_runtime(tmp_path / "fake-deck")
    rt = Runtime(
        executables={
            **deck_rt.executables,
            **report_rt.executables,
            "deckifyr_python": deck_rt.executable("deckifyr_python"),
        },
        r_library=report_rt.r_library,
    )
    with publish_client(security, tmp_path, study, rt) as client:
        client.fake = tmp_path / "fake"
        client.fake_deck = tmp_path / "fake-deck"
        client.app.state.supervisor = StubSupervisor()
        yield client
    writable(study)


def install(api, tmp_path, select, **kw):
    src = mixed_bundle(tmp_path / "src", **kw)
    text = (src / "standards.yaml").read_text("utf-8")
    (src / "report/standard_footnotes.yaml").write_bytes(HOUSE_FOOTNOTES)
    (src / "standards.yaml").write_text(text.replace(sha(FOOTNOTES), sha(HOUSE_FOOTNOTES)), "utf-8")
    preview = api.post(
        "/api/standards/inspect", json={"source": {"kind": "path", "path": str(src)}}
    ).json()
    assert preview["problems"] == [], preview["problems"]
    out = api.post(
        "/api/standards/install", json={"preview_id": preview["preview_id"], "select": select}
    )
    assert out.status_code == 200, out.text
    return out.json()


def publication(api):
    a, _ = completed_run(api.study, "Drug A")
    items = picks(a, ("summary", "profiles-linear"))
    r = api.post("/api/nca/publications", json={"items": items})
    assert r.status_code == 201, r.text
    return r.json()["publication"]


def setup(api, tool, pub):
    out = api.post(f"/api/nca/{tool}", json={"publication_id": pub["publication_id"]})
    assert out.status_code == 200, out.text
    job = wait_job(api, out.json()["job_id"], 60)
    assert job["state"] == "completed", job["message"]


def test_report_setup_uses_the_selected_template(api, tmp_path):
    done = install(api, tmp_path, ["house-report"])
    pub = publication(api)
    setup(api, "report", pub)
    project = api.study / "report"
    assert (project / Q.FOOTNOTES).read_bytes() == HOUSE_FOOTNOTES
    assert (project / Q.CONFIG).read_bytes() == CONFIG
    snapshot = api.study / ".opncafyr/standards" / done["snapshot"]
    assert (project / Q.REFERENCE_DOCX).read_bytes() == (
        snapshot / "report/org-reference.docx"
    ).read_bytes()
    binding = json.loads((api.study / ".opncafyr/report.json").read_text("utf-8"))
    validate(binding, "publish-binding-v1")
    assert binding["standards"]["snapshot"] == done["snapshot"]
    assert binding["standards"]["component"] == "house-report"
    status = api.get("/api/nca/report").json()
    assert status["template"]["differs"] is False
    assert status["template"]["selected"]["bundle_id"] == "house"

    # A later selection doesn't change the project; the card says so.
    api.put(
        "/api/standards/selected",
        json={"type": "report-template", "snapshot": None, "component": None},
    )
    status = api.get("/api/nca/report").json()
    assert status["template"]["differs"] is True
    assert status["template"]["project"]["component"] == "house-report"


def test_report_render_records_the_template(api, tmp_path):
    install(api, tmp_path, ["house-report"])
    setup(api, "report", publication(api))
    r = api.post("/api/nca/report/render", json={"status": "draft"})
    assert r.status_code == 200, r.text
    job = wait_job(api, r.json()["job_id"], 60)
    assert job["state"] == "completed", job["message"]
    builds = sorted((api.study / "report/builds").iterdir())
    doc = json.loads((builds[-1] / "build.json").read_text("utf-8"))
    validate(doc, "publish-build-v1")
    assert doc["standards"]["component"] == "house-report"
    assert doc["runs"]  # still pinned to exact run IDs and hashes


def test_curator_checks_against_the_selected_footnotes(api, tmp_path):
    install(api, tmp_path, ["house-report"])
    from opncafyr.workflows.nca.publish.templates import footnotes_for

    assert "HOUSE" in footnotes_for(api.study).abbreviations


def test_a_style_file_is_passed_to_render(tmp_path):
    rt = fake_runtime(tmp_path / "fake")
    project = tmp_path / "p"
    project.mkdir()
    spec = Q.render_command(rt, project, "draft", 60, tmp_path / "h", tmp_path / "c")
    assert "--style" not in spec.argv
    (project / Q.STYLE).write_text("equation: {font: Cambria Math}\n")
    spec = Q.render_command(rt, project, "draft", 60, tmp_path / "h", tmp_path / "c")
    # No default style in the package: the project's file is the style.
    assert spec.argv[spec.argv.index("--style") + 1] == str(project / Q.STYLE)
    assert "--override" not in spec.argv
    # With quartifyr's default style: the project's file is merged over it.
    default = Q.package_dir(rt) / "python" / "styles" / "default.yaml"
    default.parent.mkdir(parents=True)
    default.write_text("fonts: {body: Times}\n")
    spec = Q.render_command(rt, project, "draft", 60, tmp_path / "h", tmp_path / "c")
    assert spec.argv[spec.argv.index("--style") + 1] == str(default)
    assert spec.argv[spec.argv.index("--override") + 1] == str(project / Q.STYLE)


def test_deck_setup_uses_the_selected_template(api, tmp_path):
    done = install(api, tmp_path, ["house-report", "house-deck"])
    setup(api, "deck", publication(api))
    project = api.study / "deck"
    args = json.loads((api.fake_deck / "init-args.json").read_text("utf-8"))
    snapshot = api.study / ".opncafyr/standards" / done["snapshot"]
    assert args[args.index("--from-dir") + 1] == str(snapshot / "deck")
    assert "--from-repo" not in args
    assert (project / "house-design.yaml").read_bytes() == DESIGN
    assert (project / "logo.png").read_bytes() == LOGO  # copied by the launcher
    pres = yaml.safe_load((project / D.PRESENTATION).read_text("utf-8"))
    assert pres["design"] == {"base": "house-design.yaml"}
    assert pres["layouts"] == "house-layouts.yaml"
    assert (project / D.FOOTNOTES).read_bytes() == HOUSE_FOOTNOTES
    binding = json.loads((api.study / ".opncafyr/deck.json").read_text("utf-8"))
    assert binding["standards"]["component"] == "house-deck"

    # Re-binding to a new publication keeps the template's files and names.
    pub2 = publication(api)
    out = api.post("/api/nca/deck", json={"publication_id": pub2["publication_id"]})
    assert out.status_code == 200, out.text
    pres = yaml.safe_load((project / D.PRESENTATION).read_text("utf-8"))
    assert pres["layouts"] == "house-layouts.yaml"
    binding = json.loads((api.study / ".opncafyr/deck.json").read_text("utf-8"))
    assert binding["standards"]["component"] == "house-deck"


def test_a_typed_native_deck_template_passes_its_type(api, tmp_path):
    src = tmp_path / "native"
    for path, data in deck_files("templates/nca/").items():
        (src / path).parent.mkdir(parents=True, exist_ok=True)
        (src / path).write_bytes(data)
    preview = api.post(
        "/api/standards/inspect",
        json={
            "source": {"kind": "path", "path": str(src)},
            "template": {"tool": "deckifyr", "type": "nca"},
        },
    ).json()
    assert preview["problems"] == []
    api.post(
        "/api/standards/install", json={"preview_id": preview["preview_id"], "select": ["template"]}
    ).raise_for_status()
    setup(api, "deck", publication(api))
    args = json.loads((api.fake_deck / "init-args.json").read_text("utf-8"))
    assert args[args.index("--type") + 1] == "nca"
    assert (api.study / "deck/logo.png").is_file()


def test_setup_refuses_a_changed_template(api, tmp_path):
    done = install(api, tmp_path, ["house-deck"])
    target = api.study / ".opncafyr/standards" / done["snapshot"] / "deck/house-layouts.yaml"
    target.chmod(0o644)
    target.write_text("layouts: {}\n")
    pub = publication(api)
    out = api.post("/api/nca/deck", json={"publication_id": pub["publication_id"]})
    assert out.status_code == 409 and out.json()["detail"]["code"] == "standards_changed"


def test_init_command_never_fetches(tmp_path):
    rt = fake_deck_runtime(tmp_path)
    spec = D.init_command(rt, tmp_path / "d", tmp_path / "t", "nca")
    assert spec.argv[-4:] == ["--from-dir", str(tmp_path / "t"), "--type", "nca"]
    assert spec.env["DECKIFYR_OFFLINE"] == "1"
    with pytest.raises(ValueError):
        D.init_command(rt, tmp_path / "d", None, "nca")
