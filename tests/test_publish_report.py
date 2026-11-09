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


def test_a_formatted_table_is_placed_like_a_csv_one():
    """#59: reportifyr 0.4.0 fills a ``{rpfy}:`` table from a flextable ``.rds`` too."""
    run = "20261103T142512Z-7c4e"
    items = [Q.ReportItem("a_b_summary-formatted.rds", "table", "Summary (formatted)", "A", run)]
    text = Q.report_source(
        items, "pub-20261104T090000Z-1a2b", None, [{"run_id": run, "analyte": "A"}]
    )
    lines = text.splitlines()
    i = lines.index("{rpfy}:a_b_summary-formatted.rds")
    assert lines[i - 2] == '{{< tbl_caption "Tbl1" "Summary (formatted), A" >}}'


def test_parse_render_names_the_files_whose_placeholders_stayed_unfilled():
    # #62: "Unfilled placeholders: 1." didn't say which table.
    doc = {
        "schema_version": 1,
        "status": "error",
        "error_class": "quartifyr_render_incomplete",
        "errors": [],
        "artifacts": [],
        "unfilled_placeholders": [f"{{rpfy}}:t{i}.rds" for i in range(7)],
    }
    result = Q.parse_render(1, json.dumps(doc), None, None)
    assert not result.ok
    assert "Unfilled placeholders: 7 (t0.rds, t1.rds, t2.rds, t3.rds, t4.rds and 2 more)." in (
        result.message
    )
    assert "{rpfy}" not in result.message


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


def _with_formatted_table(api, state: str):
    run, _ = completed_run(api.study, "Drug A", described=True)
    pub = publish(api, run, artifacts=("summary", "summary-formatted"))
    set_up(api, pub)
    (api.fake / "rds.txt").write_text(state, "utf-8")
    return pub, run


def test_check_names_a_captioned_formatted_table(api):
    # #62: a flextable that carries its own caption left its placeholder unfilled.
    _with_formatted_table(api, "captioned")
    job = wait_job(api, api.post("/api/nca/report/check").json()["job_id"], 60)
    assert job["state"] == "completed", job["message"]
    findings = api.get("/api/nca/report").json()["check"]["findings"]
    hit = [f for f in findings if f["check"] == "rds-captioned"]
    assert len(hit) == 1 and hit[0]["severity"] == "error"
    assert hit[0]["path"].endswith("summary-formatted.rds")
    assert "CSV" in hit[0]["message"] and "deck" in hit[0]["message"]


@pytest.mark.parametrize(
    ("state", "check"), [("not_flextable", "rds-not-flextable"), ("unreadable", "rds-unreadable")]
)
def test_check_names_an_rds_that_is_not_a_formatted_table(api, state, check):
    _with_formatted_table(api, state)
    job = wait_job(api, api.post("/api/nca/report/check").json()["job_id"], 60)
    assert job["state"] == "completed", job["message"]
    findings = api.get("/api/nca/report").json()["check"]["findings"]
    assert [f["check"] for f in findings if f["check"].startswith("rds-")] == [check]


def test_render_refuses_a_captioned_formatted_table_and_makes_no_build(api):
    _with_formatted_table(api, "captioned")
    mode(api, "ok")
    r = api.post("/api/nca/report/render", json={"status": "draft"})
    assert r.status_code == 200, r.text
    job = wait_job(api, r.json()["job_id"], 60)
    assert job["state"] == "failed"
    assert "summary-formatted.rds" in job["message"] and "CSV" in job["message"]
    assert api.get("/api/nca/report").json()["builds"] == []


def test_a_formatted_table_without_a_caption_renders(api):
    _with_formatted_table(api, "ok")
    mode(api, "ok")
    out, job = render(api)
    assert job["state"] == "completed", job["message"]


def test_a_report_without_formatted_tables_never_starts_the_rds_check(api, ready):
    (api.fake / "rds.txt").write_text("captioned", "utf-8")  # would block if it were run
    mode(api, "ok")
    out, job = render(api)
    assert job["state"] == "completed", job["message"]


def test_new_report_projects_size_figures_by_the_page_not_their_pixels(api, ready):
    # #62: opnca-r's 2000 x 1200 PNG came out about 27 inches wide.
    text = (api.study / "report" / "report" / "config.yaml").read_text("utf-8")
    assert "use_artifact_size: false" in text


def test_check_warns_about_an_older_projects_pixel_sized_figures(api, ready):
    config = api.study / "report" / "report" / "config.yaml"
    config.write_text(
        config.read_text("utf-8").replace("use_artifact_size: false", "use_artifact_size: true"),
        "utf-8",
    )
    job = wait_job(api, api.post("/api/nca/report/check").json()["job_id"], 60)
    assert job["state"] == "completed", job["message"]
    findings = api.get("/api/nca/report").json()["check"]["findings"]
    hit = [f for f in findings if f["check"] == "figure-size"]
    assert len(hit) == 1 and hit[0]["severity"] == "warning"
    assert hit[0]["path"] == "report/config.yaml"
    assert "use_artifact_size: true" in config.read_text("utf-8")  # never rewritten


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


def test_a_revision_does_not_move_the_report_or_change_its_builds(api, ready):
    # #62: the report moves to a revised publication only when asked.
    pub, a, b = ready
    mode(api, "ok")
    out, job = render(api)
    assert job["state"] == "completed", job["message"]
    before = api.get("/api/nca/report").json()
    build_file = api.study / "report" / "builds" / out["build_id"] / "build.json"
    recorded = build_file.read_bytes()

    r = api.post(
        "/api/nca/publications",
        json={"items": picks(a, ("summary",)), "revises": pub["publication_id"]},
    )
    assert r.status_code == 201, r.text
    revised = r.json()["publication"]

    after = api.get("/api/nca/report").json()
    assert after["binding"]["publication_id"] == pub["publication_id"] != revised["publication_id"]
    assert [x["build_id"] for x in after["builds"]] == [x["build_id"] for x in before["builds"]]
    assert build_file.read_bytes() == recorded
    # Binding the report to the revision is the existing, deliberate step.
    rebind = api.post("/api/nca/report", json={"publication_id": revised["publication_id"]})
    assert rebind.status_code == 200, rebind.text


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


# --- editing the report (#52) --------------------------------------------------------------

EDIT = "/api/tools/quartifyr/project"


def edit_qmd(api, change) -> str:
    doc = api.post(f"{EDIT}/read", json={"path": "report.qmd"}).json()
    new = change(doc["text"])
    out = api.post(
        f"{EDIT}/save", json={"path": "report.qmd", "text": new, "expected_sha256": doc["sha256"]}
    )
    assert out.status_code == 200, out.text
    return out.json()["sha256"]


def set_status(api, value):
    settings = api.get(EDIT).json()["settings"]
    out = api.post(
        f"{EDIT}/settings",
        json={
            "values": {**settings["values"], "status": value},
            "expected_sha256": settings["sha256"],
        },
    )
    assert out.status_code == 200, out.text
    return out.json()["sha256"]


def test_the_report_editor_offers_only_the_report_sources(api, ready):
    (api.study / "report" / "notes.md").write_text("Notes\n", "utf-8")
    doc = api.get(EDIT).json()
    assert doc["title"] == "Report source"
    assert [f["path"] for f in doc["files"]] == ["report.qmd", "notes.md"]
    assert doc["settings"]["values"] == {"status": None, "reference_doc": "bundled"}
    for path in (
        "_quarto.yml",
        "report/config.yaml",
        "report/standard_footnotes.yaml",
        "OUTPUTS/tables/x.csv",
        "builds/x/build.json",
        "_extensions/quartifyr/a.lua",
    ):
        assert api.post(f"{EDIT}/read", json={"path": path}).status_code == 403, path


def test_an_edited_report_renders_from_its_saved_source_and_settings(api, ready):
    pub, a, b = ready
    sha = edit_qmd(api, lambda t: t.replace("This report was assembled", "We assembled"))
    settings_sha = set_status(api, "final")
    out = api.post("/api/nca/report/render", json={})
    assert out.status_code == 200, out.text
    job = wait_job(api, out.json()["job_id"], 60)
    assert job["state"] == "completed", job["message"]
    build = api.get("/api/nca/report").json()["builds"][0]
    inputs = {i["role"]: i for i in build["inputs"]}
    assert inputs["shell_qmd"]["sha256"] == sha
    assert inputs["settings"]["sha256"] == settings_sha
    assert build["settings"] == {
        "status": "final",
        "status_from": "settings",
        "reference_doc": "bundled",
        "settings_sha256": settings_sha,
    }
    assert build["runs"] == [a, b] and build["outputs"][0]["role"] == "final"
    validate(build, "publish-build-v1")
    assert api.get("/api/nca/report").json()["stale_inputs"] == {build["build_id"]: []}


def test_a_render_without_a_status_asks(api, ready):
    out = api.post("/api/nca/report/render", json={})
    assert out.status_code == 409
    detail = out.json()["detail"]
    assert detail["code"] == "needs_choice" and detail["options"] == ["draft", "final"]


def test_an_edit_after_a_build_marks_it_out_of_date(api, ready):
    out, job = render(api, "draft")
    assert job["state"] == "completed"
    build_id = out["build_id"]
    build = api.get("/api/nca/report").json()["builds"][0]
    edit_qmd(api, lambda t: t + "\nA closing paragraph.\n")
    status = api.get("/api/nca/report").json()
    assert status["stale_inputs"][build_id] == ["report.qmd"]
    # The old build is untouched: same manifest, still downloadable.
    assert status["builds"][0] == build
    assert api.get(f"/api/nca/report/builds/{build_id}/docx").status_code == 200
    set_status(api, "draft")
    assert api.get("/api/nca/report").json()["stale_inputs"][build_id] == [
        "opncafyr-settings.json",
        "report.qmd",
    ]


def test_check_render_and_rebind_wait_for_unsaved_editor_text(api, ready):
    pub, _, _ = ready
    beat = {"page_id": "p1", "unsaved": ["quartifyr"]}
    assert api.post("/api/session/heartbeat", json=beat).status_code == 200
    for url, body in (
        ("/api/nca/report/render", {"status": "draft"}),
        ("/api/nca/report/check", {}),
        ("/api/nca/report", {"publication_id": pub["publication_id"]}),
    ):
        out = api.post(url, json=body)
        assert out.status_code == 409 and out.json()["detail"]["code"] == "editor_unsaved", url
    api.post("/api/session/heartbeat", json={"page_id": "p1", "unsaved": []})
    assert render(api, "draft")[1]["state"] == "completed"


def test_a_save_during_a_render_waits_for_its_copy_and_never_changes_it(api, ready, monkeypatch):
    import threading

    from opncafyr.workflows.nca.publish import projects

    files = api.app.state.project_files
    doc = api.post(f"{EDIT}/read", json={"path": "report.qmd"}).json()
    seen: dict = {}
    real = projects.copy_project

    def slow_copy(project, dest, skip):
        def edit():
            seen["saved"] = files.save(
                api.study, "quartifyr", "report.qmd", doc["text"] + "Late.\n", doc["sha256"]
            )

        thread = threading.Thread(target=edit)
        thread.start()
        thread.join(0.3)
        seen["waited"] = thread.is_alive()  # the save waits for the copy's lock
        real(project, dest, skip)
        seen["thread"] = thread

    monkeypatch.setattr(projects, "copy_project", slow_copy)
    out, job = render(api, "draft")
    seen["thread"].join(10)
    assert job["state"] == "completed", job["message"]
    assert seen["waited"] is True and "saved" in seen
    build = next(b for b in api.get("/api/nca/report").json()["builds"])
    inputs = {i["role"]: i["sha256"] for i in build["inputs"]}
    assert inputs["shell_qmd"] == doc["sha256"]  # the snapshot, not the late edit
    assert api.get("/api/nca/report").json()["stale_inputs"][out["build_id"]] == ["report.qmd"]
