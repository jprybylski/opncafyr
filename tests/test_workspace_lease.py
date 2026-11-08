"""#56: a web app that changes workspace files directly (``web_app.guard_mutations``).

Its state-changing requests are refused while a job runs or another tool has unsaved or
busy work, and otherwise hold the workspace lease, which stops jobs and project-file
saves from starting meanwhile. A child launched for one workspace is stopped when another
opens. Tested against the toy child; core never names a real tool.
"""

import sys

import pytest
from fastapi.testclient import TestClient

from opncafyr.core.app import create_app
from opncafyr.core.jobmanager import CommandSpec, JobManager, JobManagerError, JobRequest
from opncafyr.core.runtime import Runtime
from opncafyr.core.security import SESSION_HEADER, SecurityConfig
from opncafyr.core.supervisor import ChildState, Supervisor, WebAppConfig
from opncafyr.core.workspace_lease import WorkspaceLease
from toy_adapter import ToyAdapter

PORT = 54323
ORIGIN = f"http://127.0.0.1:{PORT}"


def supervisor(tmp_path, guard=True):
    apps = {
        "toy": WebAppConfig(
            adapter=ToyAdapter(), route="/apps/toy/", max_body_bytes=8 << 20, guard_mutations=guard
        ),
        "other": WebAppConfig(adapter=ToyAdapter(), route="/apps/other/"),
    }
    return Supervisor(
        apps,
        runtime=Runtime(),
        state_root=tmp_path / "state",
        log_root=tmp_path / "logs",
        ready_timeout=20.0,
        stop_grace=5.0,
    )


@pytest.fixture
def make_env(tmp_path):
    clients = []

    def make(guard=True):
        security = SecurityConfig(port=PORT)
        sup = supervisor(tmp_path, guard)
        app = create_app(security, supervisor=sup, open_tokens=tmp_path / "tokens")
        client = TestClient(app, base_url=ORIGIN, follow_redirects=False)
        client.__enter__()
        clients.append(client)
        client.headers.update(
            {"cookie": f"{security.cookie_name}={security.secret}", "origin": ORIGIN}
        )
        client.sup = sup
        client.security = security
        sup.start("toy", {})
        return client

    yield make
    for client in clients:
        client.sup.stop_all()
        client.__exit__(None, None, None)


def post(client):
    return client.post("/apps/toy/api/echo", json={"a": 1})


def test_a_guarded_post_holds_the_lease_only_while_it_runs(make_env):
    env = make_env()
    lease = env.app.state.workspace_lease
    resp = post(env)
    assert resp.status_code == 200 and resp.json()["method"] == "POST"
    assert lease.holder is None
    # Reads are never guarded.
    assert env.get("/apps/toy/api/health").status_code == 200


def test_a_guarded_post_is_refused_while_a_job_runs(make_env, monkeypatch):
    env = make_env()
    monkeypatch.setattr(env.app.state.jobs, "active", lambda: True)
    resp = post(env)
    assert resp.status_code == 423
    assert resp.json()["error"]["code"] == "jobs_active"
    assert resp.json()["detail"]["code"] == "jobs_active"
    assert env.app.state.workspace_lease.holder is None
    # Reads still work.
    assert env.get("/apps/toy/api/health").status_code == 200


def test_a_guarded_post_is_refused_while_another_editor_is_unsaved(make_env):
    env = make_env()
    env.app.state.lifecycle.heartbeat("page-1", unsaved=["quartifyr"])
    assert post(env).json()["error"]["code"] == "editor_unsaved"
    # The tool's own unsaved flag is not a reason to refuse it.
    env.app.state.lifecycle.heartbeat("page-1", unsaved=["toy"])
    assert post(env).status_code == 200


def test_a_guarded_post_is_refused_while_another_child_is_busy(make_env, monkeypatch):
    from opncafyr.core.adapter import ChildActivity

    env = make_env()
    monkeypatch.setattr(
        env.sup, "activity", lambda: {"other": ChildActivity(unsaved=False, busy=True)}
    )
    resp = post(env)
    assert resp.status_code == 423 and resp.json()["error"]["code"] == "tool_busy"


def test_a_guarded_post_is_refused_while_someone_else_holds_the_lease(make_env):
    env = make_env()
    lease = env.app.state.workspace_lease
    assert lease.acquire("someone")
    resp = post(env)
    assert resp.status_code == 423 and resp.json()["error"]["code"] == "workspace_busy"
    lease.release("someone")
    assert post(env).status_code == 200


def test_an_unguarded_app_is_never_refused(make_env, monkeypatch):
    env = make_env(guard=False)
    monkeypatch.setattr(env.app.state.jobs, "active", lambda: True)
    assert post(env).status_code == 200


def test_project_file_saves_wait_for_the_lease(make_env):
    env = make_env()
    env.app.state.workspace_lease.acquire("toy")
    resp = env.post(
        "/api/tools/quartifyr/project/save",
        json={"path": "report.qmd", "text": "x", "expected_sha256": "0" * 64},
        headers={SESSION_HEADER: env.security.secret},
    )
    assert resp.status_code == 423
    assert resp.json()["detail"]["code"] == "workspace_busy"


def test_jobs_do_not_start_while_the_lease_is_held(tmp_path):
    root = tmp_path / "study"
    (root / ".opncafyr").mkdir(parents=True)
    jm = JobManager(poll_interval=0.05)
    jm.open(root)
    jm.lease = WorkspaceLease()
    jm.lease.acquire("toy")
    request = JobRequest(
        kind="toy.run",
        label="toy",
        timeout_s=10,
        build=lambda ctx: CommandSpec([sys.executable, "-c", "pass"]),
    )
    with pytest.raises(JobManagerError, match="toy is changing workspace files"):
        jm.submit(request)
    jm.lease.release("toy")
    job_id = jm.submit(request)
    assert job_id
    jm.shutdown(timeout=20)


def test_a_child_bound_to_one_workspace_stops_when_another_opens(tmp_path):
    sup = supervisor(tmp_path)
    try:
        sup.start("toy", {"workspace_root": str(tmp_path / "a")})
        sup.start("other", {})
        assert sup.stop_bound_elsewhere(tmp_path / "a") == []
        assert sup.state("toy") is ChildState.RUNNING
        assert sup.stop_bound_elsewhere(tmp_path / "b") == ["toy"]
        assert sup.state("toy") is ChildState.STOPPED
        assert not sup.can_restart("toy")
        # A child without a workspace binding is left alone.
        assert sup.state("other") is ChildState.RUNNING
    finally:
        sup.stop_all()
