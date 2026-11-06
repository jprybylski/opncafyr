"""Read-only runs and publications listing for the home page (#49)."""

import json
import os
import sys

import pytest
from fastapi.testclient import TestClient

from artifact_docs import publication, run_manifest, write_publication, write_run
from opncafyr.core.app import create_app
from opncafyr.core.artifacts import INCOMPLETE, list_publications, list_runs
from opncafyr.core.jobmanager import JobManager
from opncafyr.core.security import SESSION_HEADER
from opncafyr.core.workspace import RecentWorkspaces

OLD = "20261101T080000Z-aaaa"
NEW = "20261103T142512Z-7c4e"


def _snapshot(root):
    return {
        p.relative_to(root).as_posix(): (p.stat().st_mtime_ns, p.stat().st_size)
        for p in root.rglob("*")
    }


def test_valid_runs_are_listed_newest_first(tmp_path):
    write_run(tmp_path, run_manifest(OLD, state="failed"))
    write_run(tmp_path, run_manifest(NEW, analyte={"column": "PARAM", "value": "A", "label": "x"}))
    runs = list_runs(tmp_path)
    assert [r["run_id"] for r in runs] == [NEW, OLD]
    assert runs[0] == {
        "run_id": NEW,
        "created_at": "2026-11-03T14:25:12Z",
        "state": "completed",
        "analyte": {"column": "PARAM", "value": "A", "label": "x"},
    }
    assert runs[1]["state"] == "failed"
    # No file paths reach the page.
    assert "path" not in json.dumps(runs)


def test_run_without_manifest_is_incomplete(tmp_path):
    write_run(tmp_path, None, run_id=NEW)
    assert list_runs(tmp_path) == [{"run_id": NEW, "state": INCOMPLETE}]


def test_invalid_manifest_is_incomplete(tmp_path):
    doc = run_manifest(NEW)
    del doc["analyte"]
    write_run(tmp_path, doc)
    assert list_runs(tmp_path)[0]["state"] == INCOMPLETE


def test_unparseable_manifest_is_incomplete(tmp_path):
    folder = write_run(tmp_path, None, run_id=NEW)
    (folder / "manifest.json").write_text("{not json", "utf-8")
    assert list_runs(tmp_path)[0]["state"] == INCOMPLETE


def test_manifest_naming_another_run_is_incomplete(tmp_path):
    write_run(tmp_path, run_manifest(OLD), run_id=NEW)
    assert list_runs(tmp_path) == [{"run_id": NEW, "state": INCOMPLETE}]


def test_files_and_hidden_entries_in_runs_are_skipped(tmp_path):
    write_run(tmp_path, run_manifest(NEW))
    runs_dir = tmp_path / ".opncafyr" / "runs"
    (runs_dir / "notes.txt").write_text("x")
    (runs_dir / ".tmp-run").mkdir()
    assert [r["run_id"] for r in list_runs(tmp_path)] == [NEW]


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_symlinks_leaving_the_workspace_are_ignored(tmp_path):
    outside = tmp_path / "outside"
    write_run(outside, run_manifest(NEW))
    write_publication(outside, publication())
    root = tmp_path / "study"
    runs_dir = root / ".opncafyr" / "runs"
    runs_dir.mkdir(parents=True)
    os.symlink(outside / ".opncafyr" / "runs" / NEW, runs_dir / NEW)
    pubs = root / ".opncafyr" / "publications"
    pubs.mkdir()
    os.symlink(outside / ".opncafyr" / "publications" / "pub-1.json", pubs / "pub-1.json")
    assert list_runs(root) == []
    assert list_publications(root) == []


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_runs_folder_symlinked_outside_is_ignored(tmp_path):
    outside = tmp_path / "outside"
    write_run(outside, run_manifest(NEW))
    root = tmp_path / "study"
    (root / ".opncafyr").mkdir(parents=True)
    os.symlink(outside / ".opncafyr" / "runs", root / ".opncafyr" / "runs")
    assert list_runs(root) == []


def test_publications_are_listed(tmp_path):
    write_publication(tmp_path, publication("pub-1"))
    write_publication(tmp_path, {"schema_version": "1.0"}, name="broken")
    (tmp_path / ".opncafyr" / "publications" / "readme.md").write_text("x")
    assert list_publications(tmp_path) == [
        {
            "publication_id": "pub-1",
            "created_at": "2026-11-04T09:00:00Z",
            "state": "recorded",
            "items": 1,
        },
        {"publication_id": "broken", "state": INCOMPLETE},
    ]


def test_missing_folders_list_nothing(tmp_path):
    assert list_runs(tmp_path) == []
    assert list_publications(tmp_path) == []


def test_listing_never_writes(tmp_path):
    write_run(tmp_path, run_manifest(NEW))
    write_run(tmp_path, None, run_id=OLD)
    write_publication(tmp_path, publication())
    before = _snapshot(tmp_path)
    list_runs(tmp_path)
    list_publications(tmp_path)
    assert _snapshot(tmp_path) == before


# --- API -------------------------------------------------------------------------------


@pytest.fixture
def api(security, tmp_path):
    study = tmp_path / "Study"
    study.mkdir()
    jobs = JobManager(poll_interval=0.05)
    recent = RecentWorkspaces(tmp_path / "state" / "recent.json")
    app = create_app(security, jobs=jobs, picker=lambda prompt, start: study, recent=recent)
    with TestClient(app, base_url=f"http://127.0.0.1:{security.port}") as client:
        client.headers[SESSION_HEADER] = security.secret
        client.headers["origin"] = f"http://127.0.0.1:{security.port}"
        client.study = study
        yield client
    jobs.shutdown()


def test_api_lists_nothing_without_a_workspace(api):
    assert api.get("/api/workspace/artifacts").json() == {
        "workspace_open": False,
        "runs": [],
        "publications": [],
    }


def test_api_lists_runs_of_the_open_workspace(api):
    folder = api.post("/api/workspace/pick").json()["folder"]
    assert api.post("/api/workspace/init", json={"path": folder["path"]}).status_code == 200
    write_run(api.study, run_manifest(NEW))
    write_run(api.study, None, run_id=OLD)
    write_publication(api.study, publication())
    body = api.get("/api/workspace/artifacts").json()
    assert body["workspace_open"] is True
    assert [(r["run_id"], r["state"]) for r in body["runs"]] == [
        (NEW, "completed"),
        (OLD, INCOMPLETE),
    ]
    assert [p["publication_id"] for p in body["publications"]] == ["pub-1"]
