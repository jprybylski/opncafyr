"""File work per request stays flat as a study grows (#67).

On Windows each file open costs far more than in a Linux container (endpoint security
scans files as they are opened), so the pages' lists of runs and publications must not
re-read, re-validate or re-hash every unchanged file on every poll. These tests count
the work (``perf_helpers.count_io``), which is the same on every OS, and check that the
shortcuts never hide a change: whatever uses a run's files still hashes them.
"""

from __future__ import annotations

import json
import os

import pytest

from opncafyr.core import runs as R
from opncafyr.core.manifest import atomic_write_json, read_document
from opncafyr.core.workspace import init_workspace, open_workspace
from perf_helpers import count_io
from publish_helpers import completed_run, picks, writable

LISTS = ("/api/workspace/artifacts", "/api/nca/runs", "/api/nca/publish/runs")


@pytest.fixture
def study(tmp_path):
    root = tmp_path / "Study"
    root.mkdir()
    init_workspace(root, "0.0.1")
    yield root
    writable(root)


@pytest.fixture
def api(client, study):
    # Counts cover every thread: let the launch-time tool health checks finish first.
    assert client.app.state.health.wait(60)
    client.app.state.workspace = open_workspace(study)
    client.study = study
    return client


def publish(api, items):
    return api.post("/api/nca/publications", json={"items": items}, headers=api.session_headers)


def _modify_keeping_size_and_time(path):
    """An edit that keeps the size and modification time (deliberately evasive)."""
    st = path.stat()
    os.chmod(path, 0o644)
    data = bytearray(path.read_bytes())
    data[0] ^= 0x01
    path.write_bytes(bytes(data))
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))


def _io(api, url):
    with count_io() as c:
        assert api.get(url).status_code == 200
    return c


@pytest.mark.parametrize("url", LISTS)
def test_a_list_opens_no_file_again_while_nothing_changed(api, url):
    for i in range(8):
        completed_run(api.study, f"Drug {i}")
    api.get(url)  # the first look reads and checks everything once
    second = _io(api, url)
    assert second.open == 0, second.as_dict()
    assert second.spawn == 0


@pytest.mark.parametrize("url", LISTS)
def test_the_work_of_a_list_grows_with_runs_only_by_stats_and_listings(api, url):
    completed_run(api.study, "Drug 0")
    api.get(url)
    few = _io(api, url)
    for i in range(1, 9):
        completed_run(api.study, f"Drug {i}")
    api.get(url)
    many = _io(api, url)
    assert many.open == few.open == 0
    # A handful of stats, resolves and folder listings per run, never a file's contents.
    per_run = 9 - 1
    assert many.stat - few.stat <= 25 * per_run, (few.as_dict(), many.as_dict())
    assert many.resolve - few.resolve <= 6 * per_run, (few.as_dict(), many.as_dict())


def test_a_new_run_shows_up_in_the_lists(api):
    api.get("/api/workspace/artifacts")
    run_id, _ = completed_run(api.study, "Drug A")
    runs = api.get("/api/workspace/artifacts").json()["runs"]
    assert [r["run_id"] for r in runs] == [run_id]
    rows = api.get("/api/nca/publish/runs").json()["runs"]
    assert rows[0]["run_id"] == run_id and rows[0]["publishable"]


def test_a_changed_file_makes_a_run_unpublishable_in_the_list(api):
    _, folder = completed_run(api.study, "Drug A")
    assert api.get("/api/nca/publish/runs").json()["runs"][0]["publishable"]
    target = folder / "results" / "opnca-summary.csv"
    os.chmod(target, 0o644)
    target.write_text(target.read_text("utf-8") + "extra\n", "utf-8")
    row = api.get("/api/nca/publish/runs").json()["runs"][0]
    assert row["publishable"] is False
    assert "no longer match" in row["reason"]


def test_publishing_hashes_every_file_even_after_an_evasive_edit(api):
    run_id, folder = completed_run(api.study, "Drug A")
    assert api.get("/api/nca/publish/runs").json()["runs"][0]["publishable"]
    _modify_keeping_size_and_time(folder / "results" / "opnca-summary.csv")
    r = publish(api, picks(run_id, ["summary"]))
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["code"] == "not_publishable"
    # Check files (the run's own verify) hashes too.
    report = api.get(f"/api/runs/{run_id}/verify").json()["report"]
    assert report["ok"] is False and report["changed"] == ["results/opnca-summary.csv"]


def test_verify_run_reuses_a_full_check_only_while_nothing_changed(study):
    _, folder = completed_run(study, "Drug A")
    first = R.verify_run(folder)
    assert first["ok"] and first["sealed"]
    with count_io() as c:
        again = R.verify_run(folder, reuse=True)
    assert again == first and c.open == 0
    with count_io() as c:
        R.verify_run(folder)  # without reuse it always hashes
    assert c.open >= len(R._files(folder))
    extra = folder / "results" / "added.csv"
    os.chmod(extra.parent, 0o700)
    extra.write_text("x\n", "utf-8")
    report = R.verify_run(folder, reuse=True)
    assert report["ok"] is False and report["added"] == ["results/added.csv"]


def test_verify_run_reports_are_copies(study):
    _, folder = completed_run(study, "Drug A")
    R.verify_run(folder)
    report = R.verify_run(folder, reuse=True)
    report["changed"].append("x")
    assert R.verify_run(folder, reuse=True)["changed"] == []


def test_read_document_sees_every_rewrite_and_returns_copies(tmp_path):
    path = tmp_path / "workspace.json"
    doc = {"schema_version": "1.0", "created_at": "2026-01-01T00:00:00Z", "sources": []}
    atomic_write_json(path, {**doc, "app_version": "0.0.1"}, "workspace-v1")
    got, digest = read_document(path, "workspace-v1")
    assert got["app_version"] == "0.0.1" and len(digest) == 64
    got["app_version"] = "changed in memory"
    assert read_document(path, "workspace-v1")[0]["app_version"] == "0.0.1"
    with count_io() as c:
        read_document(path, "workspace-v1")
    assert c.open == 0
    atomic_write_json(path, {**doc, "app_version": "0.0.2"}, "workspace-v1")
    assert read_document(path, "workspace-v1")[0]["app_version"] == "0.0.2"
    # An ordinary in-place edit is seen as well.
    path.write_text(json.dumps({**doc, "app_version": "0.0.33"}), "utf-8")
    assert read_document(path, "workspace-v1")[0]["app_version"] == "0.0.33"
