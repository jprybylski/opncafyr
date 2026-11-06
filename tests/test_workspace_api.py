"""Workspace selection API: pick, inspect, init, open (#17)."""

import json
import sys
import threading
import time

import pytest
from fastapi.testclient import TestClient

from opncafyr.core.app import create_app
from opncafyr.core.jobmanager import CommandSpec, JobManager, JobRequest
from opncafyr.core.picker import PickerUnavailable
from opncafyr.core.security import SESSION_HEADER
from opncafyr.core.workspace import RecentWorkspaces

SLEEP = JobRequest(
    kind="toy.sleep",
    label="sleep",
    timeout_s=60,
    build=lambda ctx: CommandSpec([sys.executable, "-c", "import time; time.sleep(600)"]),
)


class FakePicker:
    def __init__(self, result=None, error=None, gate=None):
        self.result = result
        self.error = error
        self.gate = gate
        self.calls = []

    def __call__(self, prompt, start):
        self.calls.append((prompt, start))
        if self.gate is not None:
            self.gate.wait(10)
        if self.error is not None:
            raise self.error
        return self.result


@pytest.fixture
def jobs():
    jm = JobManager(poll_interval=0.05)
    yield jm
    jm.shutdown()


@pytest.fixture
def recent(tmp_path):
    return RecentWorkspaces(tmp_path / "state" / "recent-workspaces.json")


@pytest.fixture
def study(tmp_path):
    folder = tmp_path / "Study A"
    folder.mkdir()
    (folder / "pk.csv").write_text("USUBJID,TIME,CONC\n", "utf-8")
    return folder


def _client(security, jobs, recent, picker):
    app = create_app(security, jobs=jobs, picker=picker, recent=recent)
    client = TestClient(
        app,
        base_url=f"http://127.0.0.1:{security.port}",
        cookies={security.cookie_name: security.secret},
    )
    client.headers[SESSION_HEADER] = security.secret
    return client


@pytest.fixture
def make_client(security, jobs, recent):
    clients = []

    def make(picker):
        c = _client(security, jobs, recent, picker)
        c.__enter__()
        clients.append(c)
        return c

    yield make
    for c in clients:
        c.__exit__(None, None, None)


def test_pick_inspect_init_open_flow(make_client, study, jobs, recent):
    picker = FakePicker(result=study)
    api = make_client(picker)
    assert api.get("/api/workspace").json() == {"workspace": None, "recent": []}

    picked = api.post("/api/workspace/pick").json()
    assert picked["cancelled"] is False
    folder = picked["folder"]
    assert folder["state"] == "new"
    assert folder["path"] == str(study.resolve())
    assert folder["entries"] == 1
    assert not (study / ".opncafyr").exists()  # picking and inspecting write nothing

    again = api.post("/api/workspace/inspect", json={"path": folder["path"]}).json()
    assert again["folder"] == folder

    opened = api.post("/api/workspace/init", json={"path": folder["path"]}).json()
    assert opened["workspace"]["path"] == str(study.resolve())
    assert opened["workspace"]["schema_version"] == "1.0"
    assert jobs.workspace == study.resolve()
    assert (study / ".opncafyr" / "workspace.json").is_file()

    state = api.get("/api/workspace").json()
    assert state["workspace"]["name"] == "Study A"
    assert [r["path"] for r in state["recent"]] == [str(study.resolve())]
    assert state["recent"][0]["available"] is True

    # Opening again (now from the recent list, no picker needed) works.
    assert api.post("/api/workspace/open", json={"path": folder["path"]}).status_code == 200
    # Initializing an existing workspace does not.
    r = api.post("/api/workspace/init", json={"path": folder["path"]})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "exists"


def test_the_picker_starts_next_to_the_last_workspace(make_client, study, recent):
    recent.remember(study.resolve())
    picker = FakePicker(result=None)
    api = make_client(picker)
    assert api.post("/api/workspace/pick").json() == {"cancelled": True, "folder": None}
    assert picker.calls[0][1] == study.resolve().parent


def test_unapproved_paths_are_refused(make_client, study, tmp_path):
    api = make_client(FakePicker(result=study))
    other = tmp_path / "other"
    other.mkdir()
    for endpoint in ("inspect", "init", "open"):
        r = api.post(f"/api/workspace/{endpoint}", json={"path": str(other)})
        assert r.status_code == 403, endpoint
        assert r.json()["detail"]["code"] == "not_approved"
    assert not (other / ".opncafyr").exists()


def test_post_endpoints_need_the_session_secret(make_client, study):
    api = make_client(FakePicker(result=study))
    del api.headers[SESSION_HEADER]
    for endpoint in ("pick", "inspect", "init", "open"):
        r = api.post(f"/api/workspace/{endpoint}", json={"path": str(study)})
        assert r.status_code == 403, endpoint
    assert not (study / ".opncafyr").exists()


def test_cross_origin_requests_are_refused(make_client, study):
    api = make_client(FakePicker(result=study))
    r = api.post("/api/workspace/pick", headers={"origin": "http://evil.example"})
    assert r.status_code == 403


def test_only_one_picker_at_a_time(make_client, study):
    gate = threading.Event()
    picker = FakePicker(result=study, gate=gate)
    api = make_client(picker)
    results = {}
    first = threading.Thread(target=lambda: results.update(a=api.post("/api/workspace/pick")))
    first.start()
    deadline = time.monotonic() + 10
    while not picker.calls and time.monotonic() < deadline:
        time.sleep(0.01)
    second = api.post("/api/workspace/pick")
    gate.set()
    first.join(10)
    assert second.status_code == 409
    assert second.json()["detail"]["code"] == "picker_busy"
    assert results["a"].status_code == 200


def test_picker_unavailable_is_501(make_client):
    api = make_client(FakePicker(error=PickerUnavailable("No picker here.")))
    r = api.post("/api/workspace/pick")
    assert r.status_code == 501
    assert r.json()["detail"] == {"code": "picker_unavailable", "message": "No picker here."}


def test_future_schema_is_refused_through_the_api(make_client, study, jobs):
    (study / ".opncafyr").mkdir()
    (study / ".opncafyr" / "workspace.json").write_text(
        json.dumps({"schema_version": "7.0", "created_at": "2031-01-01T00:00:00Z"}), "utf-8"
    )
    api = make_client(FakePicker(result=study))
    assert api.post("/api/workspace/pick").json()["folder"]["state"] == "newer"
    r = api.post("/api/workspace/open", json={"path": str(study), "migrate": True})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "newer"
    assert "Update opncafyr" in r.json()["detail"]["message"]
    assert jobs.workspace is None


def test_migration_needs_consent(make_client, study):
    meta = study / ".opncafyr"
    meta.mkdir()
    (meta / "workspace.json").write_text(
        json.dumps({"schema_version": "0", "created": "2026-01-01T00:00:00Z"}), "utf-8"
    )
    api = make_client(FakePicker(result=study))
    assert api.post("/api/workspace/pick").json()["folder"]["state"] == "needs_migration"
    r = api.post("/api/workspace/open", json={"path": str(study)})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "needs_migration"
    assert not (meta / "backups").exists()

    body = api.post("/api/workspace/open", json={"path": str(study), "migrate": True}).json()
    assert body["migrated_from"] == "0"
    (backup,) = body["backups"]
    assert backup.startswith(".opncafyr/backups/workspace-v0-")
    assert body["workspace"]["schema_version"] == "1.0"


def test_open_runs_the_job_sweep(make_client, study, jobs):
    api = make_client(FakePicker(result=study))
    api.post("/api/workspace/pick")
    api.post("/api/workspace/init", json={"path": str(study)})
    # A job left 'running' by a launcher that died.
    job_id = "20260101T000000Z-abcdef"
    job_dir = study / ".opncafyr" / "jobs" / job_id
    job_dir.mkdir(parents=True)
    record = {
        "schema_version": 1,
        "job_id": job_id,
        "kind": "toy.sleep",
        "label": "sleep",
        "pool": "default",
        "state": "running",
        "message": "Running",
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
        "timeout_s": 60,
    }
    (job_dir / "state.json").write_text(json.dumps(record), "utf-8")
    body = api.post("/api/workspace/open", json={"path": str(study)}).json()
    assert body["swept_jobs"] == [job_id]
    assert jobs.get(job_id)["state"] == "failed"


def test_switching_while_a_job_runs_is_refused(make_client, study, tmp_path, jobs):
    second = tmp_path / "Study B"
    second.mkdir()
    picker = FakePicker(result=study)
    api = make_client(picker)
    api.post("/api/workspace/pick")
    api.post("/api/workspace/init", json={"path": str(study)})
    picker.result = second
    api.post("/api/workspace/pick")
    job_id = jobs.submit(SLEEP)
    try:
        r = api.post("/api/workspace/init", json={"path": str(second)})
        assert r.status_code == 409
        assert r.json()["detail"]["code"] == "jobs_active"
        assert not (second / ".opncafyr").exists()
        assert jobs.workspace == study.resolve()
    finally:
        jobs.cancel(job_id)
        jobs.wait(job_id, 20)
