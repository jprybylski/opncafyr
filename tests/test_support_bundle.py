"""#38: the redacted support bundle holds what support needs and nothing it shouldn't.

The acceptance test builds a study from the two-analyte fixture, runs jobs whose output and
messages quote subject IDs and concentrations, writes unredacted launcher and child logs,
and then checks that no fixture subject ID, concentration, study path, home folder or user
name reaches the bundle.
"""

from __future__ import annotations

import csv
import json
import os
import shutil
import sys
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from installer_world import run as install
from opncafyr import cli
from opncafyr.core import support
from opncafyr.core.app import create_app
from opncafyr.core.jobmanager import CommandSpec, JobManager, JobRequest
from opncafyr.core.jobs import JobState
from opncafyr.core.redaction import REDACTED
from opncafyr.core.registry import load_descriptors, load_workflows
from opncafyr.core.runtime import Runtime
from opncafyr.core.security import SESSION_HEADER, SecurityConfig
from opncafyr.core.workspace import RecentWorkspaces
from opncafyr.installer.layout import Layout
from opncafyr.installer.verify import InstallError
from opncafyr.workflows.nca.sensitive import sensitive_values

FIXTURE = Path(__file__).parent / "fixtures" / "two_analyte.csv"
PORT = 54341
PY = sys.executable


def _fixture() -> tuple[set[str], set[str]]:
    """The fixture's subject IDs (USUBJID) and its decimal concentrations."""
    with FIXTURE.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    ids = {r["USUBJID"] for r in rows if r["USUBJID"]}
    concs = {r["CONC"] for r in rows if "." in r["CONC"] or "," in r["CONC"]}
    return ids, concs


@pytest.fixture
def app_root(tmp_path, monkeypatch):
    root = tmp_path / "app root"
    monkeypatch.setenv("OPNCAFYR_APP_ROOT", str(root))
    return root


@pytest.fixture
def study(tmp_path):
    """A study with the fixture, a recipe that maps subject IDs, and its cleaned output."""
    root = tmp_path / "Study PK-101"
    meta = root / ".opncafyr"
    (meta / "recipes").mkdir(parents=True)
    (root / "source-data").mkdir()
    shutil.copyfile(FIXTURE, root / "source-data" / "pk.csv")
    recipe = {
        "schema_version": "1.0",
        "id": "pk-csv-1a2b",
        "source_id": "source-data/pk.csv",
        "steps": [],
        "mapping": {"roles": {"SUBJECT_ID": "USUBJID", "SECONDARY_ID": "SUBJID"}},
    }
    (meta / "recipes" / "pk-csv-1a2b.yaml").write_text(json.dumps(recipe), "utf-8")
    cleaned = meta / "cleaned" / "pk-csv-1a2b-0123abcd"
    cleaned.mkdir(parents=True)
    shutil.copyfile(FIXTURE, cleaned / "cleaned.csv")
    (meta / "runs").mkdir()
    return root


def _noisy_jobs(study: Path) -> None:
    """A completed job that printed rows, and a failed one whose message quotes data."""
    rows = FIXTURE.read_text("utf-8").splitlines()[1:6]
    code = "import sys; print(sys.argv[2]); print(sys.argv[2], file=sys.stderr)"
    jobs = JobManager(poll_interval=0.05)
    jobs.open(study)
    try:
        ok = jobs.submit(
            JobRequest(
                kind="toy.print",
                label="pk.csv rows",
                timeout_s=30,
                build=lambda ctx: CommandSpec([PY, "-c", code, "-", "\n".join(rows)]),
            )
        )
        bad = jobs.submit(
            JobRequest(
                kind="toy.fail",
                label="source-data/pk.csv",
                timeout_s=30,
                build=lambda ctx: CommandSpec([PY, "-c", "import sys; sys.exit(4)"]),
                explain=lambda c: (
                    f"Error in {study}/source-data/pk.csv: subject TA-003 has CONC 124.137, "
                    "TA-007 has 12,4 and -0.020"
                ),
            )
        )
        assert jobs.wait(ok, timeout=30) == JobState.COMPLETED
        assert jobs.wait(bad, timeout=30) == JobState.FAILED
    finally:
        jobs.shutdown(timeout=20)


def _noisy_logs(app_root: Path, study: Path) -> None:
    logs = app_root / "logs"
    (logs / "children").mkdir(parents=True)
    lines = [
        {"ts": "2026-11-07T10:00:00", "level": "INFO", "logger": "x", "message": f"opened {study}"},
        {
            "ts": "2026-11-07T10:00:01",
            "level": "ERROR",
            "logger": "x",
            "message": "TA-001 at 124.137",
        },
        {"ts": "2026-11-07T10:00:02", "level": "ERROR", "logger": "x", "exception": "TA-010 1.656"},
    ]
    (logs / "launcher.log").write_text("\n".join(json.dumps(x) for x in lines) + "\n", "utf-8")
    (logs / "children" / "opnca.log").write_text(
        f"listening\nrow TA-002,124.137 from {study}\nhome {Path.home()}\n", "utf-8"
    )
    state = app_root / "state"
    state.mkdir(parents=True)
    RecentWorkspaces(state / "recent-workspaces.json").remember(study)


def _all_text(entries) -> str:
    return "\n".join(e.text for e in entries)


# --- scrubbing rules -------------------------------------------------------------------


def test_free_text_masks_decimals_but_keeps_versions_and_job_ids():
    s = support.Scrubber()
    text = (
        "conc 124.137, 12,4, -0.020 and 1.5e-3; R 4.6.2, opnca 0.2.9; job 20261107T120001Z-ab12cd"
    )
    assert s.free(text) == (
        "conc ‹n›, ‹n›, ‹n› and ‹n›; R 4.6.2, opnca 0.2.9; job 20261107T120001Z-ab12cd"
    )


def test_paths_home_user_and_secrets(tmp_path):
    study = tmp_path / "Study"
    study.mkdir()
    s = support.Scrubber(workspace=study, home=str(tmp_path), user="alice")
    out = s.path(f"{study}/x.csv and {tmp_path}/other by alice token=abc ghp_{'a' * 30}")
    assert out == f"<study:Study>/x.csv and ~/other by <user> token={REDACTED} <token>"


def test_registered_values_are_masked_as_whole_tokens():
    s = support.Scrubber(values=["TA-003", "040"])
    assert (
        s.free("TA-003 and TA-0031 and 040, 1040") == f"{REDACTED} and TA-0031 and {REDACTED}, 1040"
    )


def test_the_nca_hook_finds_mapped_identifiers(study):
    ids, _ = _fixture()
    found = sensitive_values(study)
    assert ids <= found
    assert "040" in found  # the SUBJID typo the fixture plants


def test_the_nca_hook_ignores_broken_files(study):
    (study / ".opncafyr" / "recipes" / "broken.yaml").write_text(": : :", "utf-8")
    (study / ".opncafyr" / "cleaned" / "pk-csv-1a2b-0123abcd" / "rejected.csv").write_bytes(
        b"\xff\x00"
    )
    assert sensitive_values(study)


# --- acceptance ------------------------------------------------------------------------


def test_bundle_after_the_fixture_has_no_subject_ids_or_concentrations(app_root, study):
    _noisy_jobs(study)
    _noisy_logs(app_root, study)
    packs = load_workflows()
    entries = support.build(
        app_root=app_root,
        tools=load_descriptors(),
        runtime=Runtime(),
        workspace=study,
        sensitive=support.sensitive_values(packs, study),
    )
    names = [e.name for e in entries]
    assert names == [*support.ENTRIES, "logs/children/opnca.log"]
    text = _all_text(entries)
    ids, concs = _fixture()
    assert [i for i in ids if i in text] == []
    assert [c for c in concs if c in text] == []
    assert str(study) not in text and str(study.resolve()) not in text
    assert str(Path.home()) not in text
    user = os.environ.get("USER") or os.environ.get("USERNAME")
    if user and len(user) > 2:
        assert user not in text

    jobs = json.loads(next(e.text for e in entries if e.name == "jobs.json"))
    assert jobs["workspace"] == "<study:Study PK-101>"
    assert jobs["counts"]["recipes"] == 1 and jobs["counts"]["jobs"] == 2
    failed = next(j for j in jobs["recent_jobs"] if j["state"] == "failed")
    assert failed["exit_code"] == 4 and failed["kind"] == "toy.fail"
    assert "label" not in failed and "output" not in failed
    assert failed["message"] == (
        f"Error in <study:Study PK-101>/source-data/pk.csv: subject {REDACTED} has CONC ‹n›, "
        f"{REDACTED} has ‹n› and ‹n›"
    )
    done = next(j for j in jobs["recent_jobs"] if j["state"] == "completed")
    assert done["logs"]["stdout.log"] > 0  # sizes only, never the content


def test_saved_zip_holds_exactly_the_previewed_entries(app_root, study):
    entries = support.build(app_root=app_root, tools={}, workspace=study)
    path = support.write(entries, app_root)
    assert support.BUNDLE_NAME_RE.match(path.name)
    assert path.parent == app_root / "logs" / "support"
    with zipfile.ZipFile(path) as zf:
        assert {n: zf.read(n).decode() for n in zf.namelist()} == {e.name: e.text for e in entries}
    second = support.write(entries, app_root)
    assert second != path and second.exists()


# --- a failed Gate 0 install is diagnosable from the bundle -----------------------------


def test_bundle_shows_a_damaged_install(tmp_path, world, fakes, monkeypatch):
    root = tmp_path / "app root"
    layout = Layout(root, tmp_path / "rroot")
    assert install(layout, world.payload, assets=[world.assets]) == 0
    install_id = layout.read_current()
    runtime = json.loads((layout.version_dir(install_id) / "runtime" / "runtime.json").read_text())
    Path(runtime["executables"]["rscript"]).unlink()
    payload_copy = layout.version_dir(install_id) / "installer" / "runtime" / "pins.toml"
    payload_copy.write_text(payload_copy.read_text() + "\n# edited\n")
    monkeypatch.setenv("OPNCAFYR_INSTALL_FAIL_AFTER", "staging")
    with pytest.raises(InstallError):
        install(layout, world.payload, assets=[world.assets], reinstall=True)

    entries = {e.name: e.text for e in support.build(app_root=root, tools={})}
    doc = json.loads(entries["install.json"])
    assert doc["active"] == install_id
    assert any(p.startswith("rscript:") and "missing" in p for p in doc["problems"])
    assert doc["payload"]["ok"] is False
    assert doc["record"]["components"] and doc["record"]["smoke"] is not None
    assert doc["install_failure_bundles"]
    assert doc["latest_install_log"]["files"]
    assert "simulated failure" in entries["logs/install/installer.log"]
    system = json.loads(entries["system.json"])
    assert system["os"] and system["machine"] and system["install_id"] == install_id


# --- API -------------------------------------------------------------------------------


@pytest.fixture
def api(app_root, study):
    security = SecurityConfig(port=PORT)
    jobs = JobManager(poll_interval=0.05)
    app = create_app(
        security,
        jobs=jobs,
        picker=lambda prompt, start: study,
        recent=RecentWorkspaces(app_root / "recent.json"),
        runtime=Runtime(),
    )
    with TestClient(
        app,
        base_url=f"http://127.0.0.1:{PORT}",
        cookies={security.cookie_name: security.secret},
    ) as client:
        client.security = security
        client.headers[SESSION_HEADER] = security.secret
        client.post("/api/workspace/pick")
        client.post("/api/workspace/init", json={"path": str(study)})
        assert client.post("/api/workspace/open", json={"path": str(study)}).status_code == 200
        yield client
    jobs.shutdown(timeout=20)


def test_preview_then_save_writes_the_preview(api, app_root):
    preview = api.get("/api/support/preview").json()
    names = [e["name"] for e in preview["entries"]]
    assert names[: len(support.ENTRIES)] == list(support.ENTRIES)
    jobs = json.loads(next(e["text"] for e in preview["entries"] if e["name"] == "jobs.json"))
    assert jobs["workspace"] == "<study:Study PK-101>"
    saved = api.post("/api/support/bundle", json={"preview_id": preview["preview_id"]}).json()
    with zipfile.ZipFile(app_root / "logs" / "support" / saved["name"]) as zf:
        assert zf.namelist() == names
    # A preview is saved once.
    again = api.post("/api/support/bundle", json={"preview_id": preview["preview_id"]})
    assert again.status_code == 404
    listed = api.get("/api/support/bundles").json()["bundles"]
    assert [b["name"] for b in listed] == [saved["name"]]
    download = api.get(f"/api/support/bundles/{saved['name']}")
    assert download.status_code == 200 and download.content[:2] == b"PK"


def test_saving_needs_the_session_header(api):
    preview_id = api.get("/api/support/preview").json()["preview_id"]
    resp = api.post(
        "/api/support/bundle",
        json={"preview_id": preview_id},
        headers={SESSION_HEADER: "wrong"},
    )
    assert resp.status_code == 403


def test_preview_needs_the_session_cookie(api):
    api.cookies.clear()
    assert api.get("/api/support/preview").status_code in (401, 403)


@pytest.mark.parametrize(
    "name",
    ["..%2Fstate%2Fsettings.json", "support-x.zip", "launcher.log", "support-20261107-1.zip"],
)
def test_only_bundle_names_can_be_downloaded(api, app_root, name):
    (app_root / "logs" / "support").mkdir(parents=True, exist_ok=True)
    assert api.get(f"/api/support/bundles/{name}").status_code == 404


# --- CLI -------------------------------------------------------------------------------


def test_cli_preview_saves_nothing(app_root, study, capsys):
    assert cli.main(["doctor", "--preview", "--workspace", str(study)]) == 0
    out = capsys.readouterr().out
    assert "===== jobs.json =====" in out and "nothing was saved" in out
    assert not (app_root / "logs" / "support").exists()


def test_cli_bundle_with_yes_saves(app_root, study, capsys):
    assert cli.main(["doctor", "--bundle", "--yes", "--workspace", str(study)]) == 0
    saved = list((app_root / "logs" / "support").glob("support-*.zip"))
    assert len(saved) == 1
    assert "stays on this computer" in capsys.readouterr().out


def test_cli_bundle_without_a_terminal_asks_for_yes(app_root, monkeypatch, capsys):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    assert cli.main(["doctor", "--bundle"]) == 2
    assert not (app_root / "logs" / "support").exists()


def test_cli_refuses_a_folder_that_is_not_a_study(app_root, tmp_path):
    assert cli.main(["doctor", "--preview", "--workspace", str(tmp_path)]) == 2
