"""The report workflow with a stand-in Rscript (#32): commands, jobs, failures, builds."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import psutil
import pytest

from opncafyr.adapters import quartifyr as Q
from opncafyr.core.manifest import validate
from opncafyr.core.runtime import Runtime
from process_helpers import wait_gone
from publish_helpers import completed_run, picks, publish_client, wait_job, writable

pytestmark = pytest.mark.skipif(os.name == "nt", reason="the stand-in Rscript is a shell script")
FAKE = Path(__file__).parent / "fake_rscript.py"


def fake_runtime(base: Path) -> Runtime:
    """Executables and an R library laid out like the installed runtime, all stand-ins."""
    bin_dir = base / "bin"
    bin_dir.mkdir(parents=True)
    rscript = bin_dir / "Rscript"
    rscript.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{FAKE}" "{base}" "$@"\n', "utf-8")
    rscript.chmod(0o755)
    for name in ("quarto", "uv"):
        (bin_dir / name).write_text("#!/bin/sh\nexit 0\n", "utf-8")
        (bin_dir / name).chmod(0o755)
    py = base / "tools" / ".venv" / "bin" / "python"
    py.parent.mkdir(parents=True)
    py.write_text("", "utf-8")
    lib = base / "rlib" / "quartifyr"
    for rel in ("scripts/render.R", "scripts/check.R", "templates/org-reference.docx"):
        (lib / rel).parent.mkdir(parents=True, exist_ok=True)
        (lib / rel).write_text("stand-in", "utf-8")
    return Runtime(
        executables={
            "rscript": rscript,
            "quarto": bin_dir / "quarto",
            "uv": bin_dir / "uv",
            "deckifyr_python": py,
        },
        r_library=base / "rlib",
    )


@pytest.fixture
def fake(tmp_path, monkeypatch):
    monkeypatch.setenv("OPNCAFYR_APP_ROOT", str(tmp_path / "app"))
    return fake_runtime(tmp_path / "fake")


@pytest.fixture
def api(security, tmp_path, fake):
    study = (tmp_path / "Study").resolve()
    study.mkdir()
    with publish_client(security, tmp_path, study, fake) as client:
        client.fake = tmp_path / "fake"
        yield client
    writable(study)


def mode(api, value: str) -> None:
    (api.fake / "mode.txt").write_text(value, "utf-8")


def publish(api, *runs, artifacts=("summary", "profiles-linear")):
    items = [p for run in runs for p in picks(run, artifacts)]
    r = api.post("/api/nca/publications", json={"items": items})
    assert r.status_code == 201, r.text
    return r.json()["publication"]


def set_up(api, pub) -> None:
    out = api.post("/api/nca/report", json={"publication_id": pub["publication_id"]})
    assert out.status_code == 200, out.text
    job = wait_job(api, out.json()["job_id"], 60)
    assert job["state"] == "completed", job["message"]


@pytest.fixture
def ready(api):
    a, _ = completed_run(api.study, "Drug A")
    b, _ = completed_run(api.study, "Metabolite B")
    pub = publish(api, a, b)
    set_up(api, pub)
    return pub, a, b


def render(api, status="draft"):
    r = api.post("/api/nca/report/render", json={"status": status})
    assert r.status_code == 200, r.text
    out = r.json()
    return out, wait_job(api, out["job_id"], 60)


# --- commands ------------------------------------------------------------------------------


def test_render_command_is_strict_offline_and_pinned(fake, tmp_path):
    project = tmp_path / "p"
    spec = Q.render_command(fake, project, "final", 600, tmp_path / "home", tmp_path / "cache")
    rscript = str(fake.executable("rscript"))
    assert spec.argv[:3] == [
        rscript,
        "--vanilla",
        str(fake.r_library / "quartifyr/scripts/render.R"),
    ]
    args = spec.argv[3:]
    for flag in ("--json", "--strict", "--offline"):
        assert flag in args
    assert args[args.index("--quarto-bin") + 1] == str(fake.executable("quarto"))
    assert args[args.index("--project") + 1] == str(project)
    assert args[args.index("--shell") + 1] == str(project / "report.qmd")
    assert args[args.index("--reference-doc") + 1] == str(project / "org-reference.docx")
    assert args[args.index("--status") + 1] == "final"
    assert "--recalculate-fields" not in args and "--resolve-same-page-crossrefs" not in args
    env = spec.env
    for name in ("UV_OFFLINE", "UV_NO_INDEX", "UV_NO_SYNC", "UV_NO_PROJECT", "UV_NO_CONFIG"):
        assert env[name] == "1"
    assert env["UV_PYTHON_DOWNLOADS"] == "never"
    assert env["QUARTIFYR_PYTHON_ENV"] == str(fake.executable("deckifyr_python").parent.parent)
    assert env["HOME"] == str(tmp_path / "home")
    assert "opncafyr" in (tmp_path / "home" / ".gitconfig").read_text("utf-8")
    assert env["DENO_DIR"].startswith(str(tmp_path / "cache"))
    assert env["R_LIBS"] == str(fake.r_library)
    assert spec.path_dirs[0] == fake.executable("uv").parent  # the pinned uv first
    with pytest.raises(ValueError):
        Q.render_command(fake, project, "latest", 600, tmp_path, tmp_path)


def test_report_source_keeps_data_text_out_of_code():
    evil = "```{r}\nsystem('rm -rf ~')\n``` {{< include x >}} <!-- `x` -->"
    items = [Q.ReportItem("a_b.csv", "table", "Parameter summary", evil, "20261103T142512Z-7c4e")]
    text = Q.report_source(
        items,
        "pub-20261104T090000Z-1a2b",
        evil,
        [{"run_id": "20261103T142512Z-7c4e", "analyte": evil}],
    )
    body = text.split("{{< body-start >}}", 1)[1]
    assert (
        "```" not in text and "{r}" not in text and "include" not in body.replace("include x", "")
    )
    assert body.count("{{<") == 1  # only our caption shortcode
    lines = text.splitlines()
    i = lines.index("{rpfy}:a_b.csv")
    assert lines[i + 1] == "" and lines[i + 2].startswith("From run ")  # never last in a section


def test_parse_render_decides_on_exit_code_and_status(tmp_path):
    ok = {"schema_version": 1, "status": "ok", "errors": [], "artifacts": [{"role": "draft"}]}
    assert Q.parse_render(0, json.dumps(ok), None, None).ok
    assert not Q.parse_render(1, json.dumps(ok), None, None).ok
    warn = dict(ok, status="warning")
    assert not Q.parse_render(0, json.dumps(warn), None, None).ok
    assert not Q.parse_render(0, "", None, None).ok
    assert not Q.parse_render(0, "Error: no package", None, None).ok
    err = dict(
        ok,
        status="error",
        error_class="quartifyr_quarto_timeout",
        errors=[f"timed out in {tmp_path}/x and /Users/someone/Study/report.qmd"],
    )
    msg = Q.parse_render(1, json.dumps(err), None, tmp_path).message
    assert "time limit" in msg and "/Users/someone" not in msg and str(tmp_path) not in msg


# --- the workflow --------------------------------------------------------------------------


def test_setup_writes_the_template_and_binds(api, ready):
    pub, a, b = ready
    project = api.study / "report"
    assert (project / "_extensions" / "quartifyr").is_dir()
    assert (project / "report.qmd").read_text("utf-8").count("{rpfy}:") == 4
    assert (project / "report" / "standard_footnotes.yaml").read_bytes() == (
        Q.standard_footnotes().read_bytes()
    )
    names = {p.name for p in (project / "OUTPUTS").rglob("*") if p.is_file()}
    assert names == {
        n.rpartition("/")[2] for i in pub["items"] for n in (i["published_path"], i["sidecar_path"])
    }
    status = api.get("/api/nca/report").json()
    assert status["project"] and status["binding"]["publication_id"] == pub["publication_id"]
    assert status["edited"] == [] and status["builds"] == []


def test_check_combines_launcher_and_quartifyr_findings(api, ready):
    job = wait_job(api, api.post("/api/nca/report/check").json()["job_id"], 60)
    assert job["state"] == "completed", job["message"]
    report = api.get("/api/nca/report").json()["check"]
    validate(report, "preflight-v1")
    assert [f["check"] for f in report["findings"]] == ["placeholder-last-in-section"]
    assert report["findings"][0]["path"] == "report.qmd"  # not the staging path


def test_render_builds_a_docx_with_a_manifest_of_exact_runs(api, ready):
    pub, a, b = ready
    mode(api, "ok")
    out, job = render(api)
    assert job["state"] == "completed", job["message"]
    build = api.get("/api/nca/report").json()["builds"][0]
    validate(build, "publish-build-v1")
    assert build["build_id"] == out["build_id"] and sorted(build["runs"]) == sorted([a, b])
    assert build["publication_id"] == pub["publication_id"]
    assert [i["sha256"] for i in build["items"]] == [i["published_sha256"] for i in pub["items"]]
    assert build["settings"]["status"] == "draft" and build["tools"]["quartifyr"] == "0.3.3"
    folder = api.study / "report" / "builds" / out["build_id"]
    assert sorted(p.name for p in folder.iterdir()) == [
        "build.json",
        "render-result.json",
        "report-draft.docx",
    ]
    assert not (folder.parent / f"{out['build_id']}.staging").exists()
    r = api.get(f"/api/nca/report/builds/{out['build_id']}/docx")
    assert r.status_code == 200 and r.content == (folder / "report-draft.docx").read_bytes()
    # The render saw only this publication's files, in a non-hidden folder, offline.
    seen = json.loads((api.fake / "seen.json").read_text("utf-8"))
    assert set(seen["files"]) == {
        p for i in pub["items"] for p in (i["published_path"], i["sidecar_path"])
    }
    assert "/." not in seen["cwd"] and seen["env"]["UV_OFFLINE"] == "1"
    # A changed DOCX is never served as the build.
    (folder / "report-draft.docx").write_bytes(b"tampered")
    assert api.get(f"/api/nca/report/builds/{out['build_id']}/docx").status_code == 409


def test_only_the_bound_publications_files_are_rendered(api):
    a, _ = completed_run(api.study, "Drug A")
    first = publish(api, a, artifacts=("summary",))
    second = publish(api, a, artifacts=("profiles-log",))
    set_up(api, first)
    r = api.post("/api/nca/report", json={"publication_id": second["publication_id"]})
    assert (
        r.status_code == 200 and r.json()["binding"]["publication_id"] == second["publication_id"]
    )
    mode(api, "ok")
    _, job = render(api)
    assert job["state"] == "completed", job["message"]
    seen = json.loads((api.fake / "seen.json").read_text("utf-8"))["files"]
    assert seen == sorted(
        [second["items"][0]["published_path"], second["items"][0]["sidecar_path"]]
    )


@pytest.mark.parametrize(
    ("value", "words"),
    [
        ("error", "placeholders unfilled"),
        ("missing-metadata", "Missing metadata"),
        ("garbage", "printed no result"),
        ("badhash", "doesn't match the hash"),
    ],
)
def test_a_failed_or_partial_render_leaves_no_build(api, ready, value, words):
    mode(api, value)
    out, job = render(api)
    assert job["state"] == "failed"
    assert words in job["message"]
    assert api.get("/api/nca/report").json()["builds"] == []
    assert not (api.study / "report" / "builds" / out["build_id"]).exists()


def test_a_missing_sidecar_definition_fails_the_render_job(api, ready):
    # The report's footnotes no longer define the sidecars' abbreviations: the launcher's
    # own check (#30) fails the job before quartifyr runs.
    (api.study / "report" / "report" / "standard_footnotes.yaml").write_text(
        "abbreviations: {}\n", "utf-8"
    )
    mode(api, "ok")
    _, job = render(api)
    assert job["state"] == "failed" and "metadata problem" in job["message"]
    assert not (api.fake / "seen.json").exists()


def test_a_changed_publication_is_refused(api, ready):
    pub, _, _ = ready
    (api.study / pub["items"][0]["published_path"]).write_text("edited", "utf-8")
    for path, body in (
        ("/api/nca/report/render", {"status": "draft"}),
        ("/api/nca/report/check", None),
    ):
        r = api.post(path, json=body)
        assert r.status_code == 409 and r.json()["detail"]["code"] == "publication_changed"


def test_rebinding_over_an_edited_report_asks_first(api, ready):
    pub, a, _ = ready
    qmd = api.study / "report" / "report.qmd"
    qmd.write_text(qmd.read_text("utf-8") + "\nMy own paragraph.\n", "utf-8")
    assert api.get("/api/nca/report").json()["edited"] == ["report.qmd"]
    other = publish(api, a, artifacts=("listing",))
    body = {"publication_id": other["publication_id"]}
    r = api.post("/api/nca/report", json=body)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "needs_choice"
    assert r.json()["detail"]["options"] == ["replace", "keep"]
    kept = api.post("/api/nca/report", json={**body, "source": "keep"})
    assert kept.status_code == 200 and "My own paragraph." in qmd.read_text("utf-8")
    assert api.get("/api/nca/report").json()["edited"] == ["report.qmd"]
    replaced = api.post("/api/nca/report", json={**body, "source": "replace"})
    assert replaced.status_code == 200 and "My own paragraph." not in qmd.read_text("utf-8")
    assert api.get("/api/nca/report").json()["edited"] == []


def test_a_hung_render_is_killed_at_the_time_limit_with_its_tree(api, ready):
    mode(api, "hang")
    service, manager = api.app.state.report, api.app.state.jobs
    out = service.render(manager, api.study, "draft", timeout_s=3)
    pids = api.fake / "pids.txt"
    deadline = time.monotonic() + 20
    while not pids.is_file() and time.monotonic() < deadline:
        time.sleep(0.05)
    parent, child = map(int, pids.read_text("utf-8").split())
    procs = [psutil.Process(parent), psutil.Process(child)]
    job = wait_job(api, out["job_id"], 60)
    assert job["state"] == "failed" and job["reason"] == "timeout"
    assert wait_gone(procs, 10) == []
    assert api.get("/api/nca/report").json()["builds"] == []


def test_one_report_job_at_a_time(api, ready):
    mode(api, "hang")
    service, manager = api.app.state.report, api.app.state.jobs
    out = service.render(manager, api.study, "draft", timeout_s=30)
    r = api.post("/api/nca/report/render", json={"status": "final"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "busy"
    manager.cancel(out["job_id"])
    assert wait_job(api, out["job_id"], 30)["state"] == "cancelled"


def test_render_needs_a_project(api):
    r = api.post("/api/nca/report/render", json={"status": "draft"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "no_project"
    assert api.post("/api/nca/report/render", json={"status": "latest"}).status_code == 422
