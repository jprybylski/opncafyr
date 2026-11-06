import sys
import time

import pytest
from fastapi.testclient import TestClient

from opncafyr.core.app import create_app
from opncafyr.core.jobmanager import CommandSpec, JobManager, JobRequest
from opncafyr.core.security import SESSION_HEADER

SLEEP = JobRequest(
    kind="toy.sleep",
    label="sleep",
    timeout_s=60,
    build=lambda ctx: CommandSpec([sys.executable, "-c", "import time; time.sleep(600)"]),
)
QUICK = JobRequest(
    kind="toy.quick",
    label="quick",
    timeout_s=60,
    build=lambda ctx: CommandSpec([sys.executable, "-c", "pass"]),
)


@pytest.fixture
def jobs(tmp_path):
    (tmp_path / ".opncafyr").mkdir()
    jm = JobManager(poll_interval=0.05)
    jm.open(tmp_path)
    return jm


@pytest.fixture
def api(security, jobs):
    app = create_app(security, jobs=jobs)
    with TestClient(app, base_url=f"http://127.0.0.1:{security.port}") as c:
        yield c


def test_no_workspace_lists_nothing(client):
    assert client.get("/api/jobs").json() == {"workspace_open": False, "jobs": []}


def test_list_and_get_with_events(api, jobs):
    job_id = jobs.submit(QUICK)
    jobs.wait(job_id, 20)
    listing = api.get("/api/jobs").json()
    assert listing["workspace_open"] is True
    assert [j["job_id"] for j in listing["jobs"]] == [job_id]
    assert "pid" not in listing["jobs"][0]

    body = api.get(f"/api/jobs/{job_id}").json()
    assert body["job"]["state"] == "completed"
    seqs = [e["seq"] for e in body["events"]]
    assert seqs[0] == 1
    later = api.get(f"/api/jobs/{job_id}", params={"after": seqs[-2]}).json()
    assert [e["seq"] for e in later["events"]] == seqs[-1:]


def test_cancel_needs_the_session_secret(api, jobs, security):
    job_id = jobs.submit(SLEEP)
    assert api.post(f"/api/jobs/{job_id}/cancel").status_code == 403
    resp = api.post(f"/api/jobs/{job_id}/cancel", headers={SESSION_HEADER: security.secret})
    assert resp.status_code == 200
    deadline = time.monotonic() + 20
    while jobs.get(job_id)["state"] != "cancelled" and time.monotonic() < deadline:
        time.sleep(0.05)
    assert jobs.get(job_id)["state"] == "cancelled"


@pytest.mark.parametrize("job_id", ["nope", "20261105T000000Z-abcdef"])
def test_unknown_job_is_404(api, security, job_id):
    assert api.get(f"/api/jobs/{job_id}").status_code == 404
    headers = {SESSION_HEADER: security.secret}
    assert api.post(f"/api/jobs/{job_id}/cancel", headers=headers).status_code == 404


def test_there_is_no_submit_endpoint(api, security):
    resp = api.post("/api/jobs", headers={SESSION_HEADER: security.secret}, json={})
    assert resp.status_code == 405


def test_stopping_the_app_cancels_running_jobs(security, jobs):
    app = create_app(security, jobs=jobs)
    with TestClient(app, base_url=f"http://127.0.0.1:{security.port}"):
        job_id = jobs.submit(SLEEP)
        deadline = time.monotonic() + 20
        while jobs.get(job_id)["state"] != "running" and time.monotonic() < deadline:
            time.sleep(0.05)
    assert jobs.get(job_id)["reason"] == "launcher_stopped"
