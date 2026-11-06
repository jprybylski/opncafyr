"""Lifecycle: heartbeats, idle shutdown, explicit Quit and the unsaved-editor block (#14)."""

from __future__ import annotations

import sys
import threading
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from opncafyr import cli
from opncafyr.core import instance
from opncafyr.core.adapter import ChildActivity
from opncafyr.core.app import create_app
from opncafyr.core.instance import read_record
from opncafyr.core.jobmanager import CommandSpec, JobManager, JobManagerError, JobRequest
from opncafyr.core.jobs import JobState
from opncafyr.core.lifecycle import (
    DEFAULT_IDLE_MINUTES,
    SETTINGS_FILE,
    Lifecycle,
    LifecycleConfig,
    Settings,
)
from opncafyr.core.runtime import Runtime
from opncafyr.core.security import SESSION_HEADER
from opncafyr.core.supervisor import Supervisor, WebAppConfig
from toy_adapter import ToyAdapter

TIMEOUT = 60.0
WAIT_FOR_FILE = (
    "import pathlib, sys, time\nwhile not pathlib.Path(sys.argv[1]).exists(): time.sleep(0.05)"
)


class Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def jobs(tmp_path):
    (tmp_path / ".opncafyr").mkdir()
    jm = JobManager(poll_interval=0.05)
    jm.open(tmp_path)
    yield jm
    jm.shutdown(timeout=10)


def gated_job(marker):
    """A job that runs until ``marker`` exists, then completes."""
    return JobRequest(
        kind="toy.gated",
        label="gated",
        timeout_s=120,
        build=lambda ctx: CommandSpec([sys.executable, "-c", WAIT_FOR_FILE, str(marker)]),
    )


def wait_running(jobs, job_id):
    deadline = time.monotonic() + 20
    while jobs.get(job_id)["state"] != "running":
        assert time.monotonic() < deadline, "job never started"
        time.sleep(0.02)


class Harness:
    def __init__(self, jobs, activity=None, timeout=TIMEOUT):
        self.calls: list[str] = []
        self.clock = Clock()
        self.activity: dict[str, ChildActivity] = activity or {}
        self.life = Lifecycle(
            jobs=jobs,
            activity=lambda: self.activity,
            stop_children=lambda: self.calls.append("children"),
            request_shutdown=lambda: self.calls.append("listener"),
            idle_timeout_s=timeout,
            heartbeat_window_s=45,
            clock=self.clock,
        )

    def check_at(self, t: float) -> None:
        self.clock.t = t
        self.life.check()

    def stopped(self, timeout: float = 20) -> bool:
        return self.life.stopped.wait(timeout)


def test_no_heartbeat_and_no_job_stops_after_the_timeout(jobs):
    h = Harness(jobs)
    h.check_at(0)  # idle from here
    h.check_at(TIMEOUT - 1)
    assert not h.life.stopped.is_set()
    h.check_at(TIMEOUT)
    assert h.stopped()
    assert h.life.stop_reason == "idle"
    # Order: stop taking jobs, then children, then the listener.
    assert h.calls == ["children", "listener"]
    with pytest.raises(JobManagerError):
        jobs.submit(gated_job("x"))


def test_heartbeats_keep_it_open_until_they_stop(jobs):
    h = Harness(jobs)
    h.clock.t = 0
    h.life.heartbeat("page1")
    h.check_at(30)
    h.check_at(30 + TIMEOUT)  # the page went quiet at 0 and expired at 45
    assert not h.life.stopped.is_set()
    h.check_at(30 + 2 * TIMEOUT)
    assert h.stopped()


def test_leave_is_a_hint_that_starts_the_timer(jobs):
    h = Harness(jobs)
    h.life.heartbeat("page1")
    h.life.leave("page1")
    h.check_at(1)
    h.check_at(1 + TIMEOUT)
    assert h.stopped()


def test_timeout_off_never_stops(jobs):
    h = Harness(jobs, timeout=None)
    h.check_at(0)
    h.check_at(10**9)
    assert not h.life.stopped.is_set()
    assert h.life.status()["idle_timeout_minutes"] is None


def test_a_running_job_prevents_idle_shutdown_until_it_completes(jobs, tmp_path):
    h = Harness(jobs)
    marker = tmp_path / "go"
    job_id = jobs.submit(gated_job(marker))
    wait_running(jobs, job_id)
    h.check_at(0)
    h.check_at(10 * TIMEOUT)
    assert not h.life.stopped.is_set()

    marker.write_text("")
    assert jobs.wait(job_id, 20) is JobState.COMPLETED
    h.check_at(11 * TIMEOUT)  # idle starts now
    assert not h.life.stopped.is_set()
    h.check_at(12 * TIMEOUT)
    assert h.stopped()


def test_closing_the_tab_with_an_active_job_does_not_stop_it(jobs, tmp_path):
    h = Harness(jobs)
    job_id = jobs.submit(gated_job(tmp_path / "never"))
    wait_running(jobs, job_id)
    h.life.heartbeat("page1")
    h.life.leave("page1")
    for t in (0, TIMEOUT, 100 * TIMEOUT):
        h.check_at(t)
    assert not h.life.stopped.is_set()
    assert jobs.get(job_id)["state"] == "running"


def test_quit_with_a_running_job_keeps_it_and_stops_after(jobs, tmp_path):
    h = Harness(jobs)
    marker = tmp_path / "go"
    job_id = jobs.submit(gated_job(marker))
    wait_running(jobs, job_id)

    result = h.life.request_quit()  # the default choice
    assert result.status == "stopping_after_jobs"
    assert result.jobs == 1
    with pytest.raises(JobManagerError):
        jobs.submit(gated_job(tmp_path / "other"))
    h.life.heartbeat("page1")  # an open page does not hold up an explicit quit
    h.check_at(1)
    assert not h.life.stopped.is_set()
    assert h.life.status()["quit_pending"] is True

    marker.write_text("")
    assert jobs.wait(job_id, 20) is JobState.COMPLETED
    h.check_at(2)
    assert h.stopped()
    assert h.life.stop_reason == "quit after jobs finished"
    assert jobs.get(job_id)["state"] == "completed"


def test_abort_quit_reopens_job_intake(jobs, tmp_path):
    h = Harness(jobs)
    marker = tmp_path / "go"
    job_id = jobs.submit(gated_job(marker))
    wait_running(jobs, job_id)
    assert h.life.request_quit().status == "stopping_after_jobs"
    assert h.life.abort_quit() is True
    marker.write_text("")
    jobs.wait(job_id, 20)
    h.check_at(1)
    assert not h.life.stopped.is_set()
    jobs.submit(gated_job(marker))  # accepted again


def test_quit_cancel_cancels_jobs_and_stops(jobs, tmp_path):
    h = Harness(jobs)
    job_id = jobs.submit(gated_job(tmp_path / "never"))
    wait_running(jobs, job_id)
    result = h.life.request_quit(jobs="cancel")
    assert result.status == "stopping"
    assert h.stopped()
    assert jobs.get(job_id)["state"] == "cancelled"
    assert h.calls == ["children", "listener"]


def test_quit_without_jobs_stops_at_once(jobs):
    h = Harness(jobs)
    assert h.life.request_quit().status == "stopping"
    assert h.stopped()


def test_a_dirty_child_blocks_idle_shutdown_and_quit_asks(jobs):
    h = Harness(jobs, activity={"deck": ChildActivity(unsaved=True, busy=False)})
    h.check_at(0)
    h.check_at(100 * TIMEOUT)
    assert not h.life.stopped.is_set()
    assert h.life.status()["unsaved"] == ["deck"]

    asked = h.life.request_quit()
    assert asked.status == "needs_choice"
    assert asked.reasons == ["unsaved_changes"]
    assert asked.tools == ["deck"]
    assert not h.life.stopped.is_set()

    assert h.life.request_quit(unsaved="save").status == "stopping"
    assert h.stopped()


def test_a_busy_child_blocks_idle_and_holds_a_quit(jobs):
    h = Harness(jobs, activity={"deck": ChildActivity(unsaved=False, busy=True)})
    h.check_at(0)
    h.check_at(100 * TIMEOUT)
    assert not h.life.stopped.is_set()
    assert h.life.request_quit().status == "stopping_after_jobs"
    h.check_at(100 * TIMEOUT + 1)
    assert not h.life.stopped.is_set()
    h.activity = {"deck": ChildActivity(unsaved=False, busy=False)}
    h.check_at(100 * TIMEOUT + 2)
    assert h.stopped()


def test_page_ids_are_checked(jobs):
    with pytest.raises(ValueError):
        Harness(jobs).life.heartbeat("../x")


# --- child activity through the supervisor --------------------------------------------


class ReportingToy(ToyAdapter):
    def __init__(self, report):
        super().__init__()
        self.report = report

    def activity(self, child, client):
        if isinstance(self.report, Exception):
            raise self.report
        return self.report


def _supervisor(tmp_path, adapter):
    apps = {"toy": WebAppConfig(adapter=adapter, route="/apps/toy/")}
    return Supervisor(
        apps,
        runtime=Runtime(),
        state_root=tmp_path / "state",
        log_root=tmp_path / "logs",
        ready_timeout=20,
        stop_grace=5,
    )


@pytest.mark.parametrize(
    ("report", "expected"),
    [
        (ChildActivity(unsaved=False, busy=True), ChildActivity(unsaved=False, busy=True)),
        # A probe that fails can't promise the edits are saved.
        (httpx.ConnectError("down"), ChildActivity(unsaved=True, busy=False)),
    ],
)
def test_supervisor_reports_child_activity(tmp_path, report, expected):
    sup = _supervisor(tmp_path, ReportingToy(report))
    assert sup.activity() == {}  # not running: nothing to ask
    try:
        sup.start("toy", {})
        assert sup.activity() == {"toy": expected}
    finally:
        sup.stop_all()


def test_adapters_without_the_hook_are_not_asked(tmp_path):
    sup = _supervisor(tmp_path, ToyAdapter())
    try:
        sup.start("toy", {})
        assert sup.activity() == {}
    finally:
        sup.stop_all()


# --- settings ---------------------------------------------------------------------------


def test_settings_default_and_persist(tmp_path):
    store = Settings(tmp_path / SETTINGS_FILE)
    assert store.idle_timeout_minutes() == DEFAULT_IDLE_MINUTES
    store.set_idle_timeout_minutes(None)
    assert store.idle_timeout_minutes() is None
    store.set_idle_timeout_minutes(15)
    assert Settings(tmp_path / SETTINGS_FILE).idle_timeout_minutes() == 15
    (tmp_path / SETTINGS_FILE).write_text('{"schema_version": "1.0", "idle_timeout_minutes": 1}')
    assert store.idle_timeout_minutes() == DEFAULT_IDLE_MINUTES  # out of range: ignored


def test_cli_idle_timeout_option():
    parse = cli.build_parser().parse_args
    assert parse(["launch", "--idle-timeout", "off"]).idle_timeout is None
    assert parse(["launch", "--idle-timeout", "5"]).idle_timeout == 5
    assert parse(["launch"]).idle_timeout is cli._SAVED
    for bad in ("4", "1441", "soon"):
        with pytest.raises(SystemExit):
            parse(["launch", "--idle-timeout", bad])


# --- API --------------------------------------------------------------------------------


@pytest.fixture
def api(security, jobs, tmp_path):
    calls: list[str] = []
    app = create_app(
        security,
        request_shutdown=lambda: calls.append("listener"),
        jobs=jobs,
        settings=Settings(tmp_path / "state" / SETTINGS_FILE),
    )
    with TestClient(
        app,
        base_url=f"http://127.0.0.1:{security.port}",
        cookies={security.cookie_name: security.secret},
    ) as c:
        c.app_ref = app
        c.calls = calls
        c.session_headers = {SESSION_HEADER: security.secret}
        yield c


def test_heartbeat_and_quit_need_the_session_secret(api):
    assert api.post("/api/session/heartbeat", json={"page_id": "p1"}).status_code == 403
    assert api.post("/api/session/leave", json={"page_id": "p1"}).status_code == 403
    assert api.post("/api/quit/abort").status_code == 403
    assert api.put("/api/settings", json={"idle_timeout_minutes": 5}).status_code == 403


def test_heartbeat_counts_a_page(api):
    ok = api.post("/api/session/heartbeat", json={"page_id": "p1"}, headers=api.session_headers)
    assert ok.status_code == 200
    assert api.get("/api/lifecycle").json()["pages"] == 1
    bad = api.post("/api/session/heartbeat", json={"page_id": "a/b"}, headers=api.session_headers)
    assert bad.status_code == 422
    api.post("/api/session/leave", json={"page_id": "p1"}, headers=api.session_headers)
    assert api.get("/api/lifecycle").json()["pages"] == 0


def test_settings_api_validates_persists_and_applies(api, tmp_path):
    status = api.get("/api/lifecycle").json()
    assert status["idle_timeout_minutes"] == DEFAULT_IDLE_MINUTES
    for bad in (4, 1441, "x"):
        resp = api.put(
            "/api/settings", json={"idle_timeout_minutes": bad}, headers=api.session_headers
        )
        assert resp.status_code == 422
    resp = api.put(
        "/api/settings", json={"idle_timeout_minutes": None}, headers=api.session_headers
    )
    assert resp.status_code == 200
    assert api.get("/api/lifecycle").json()["idle_timeout_minutes"] is None
    assert Settings(tmp_path / "state" / SETTINGS_FILE).idle_timeout_minutes() is None


def test_quit_api_asks_about_unsaved_edits(api):
    life = api.app_ref.state.lifecycle
    life._activity = lambda: {"deckifyr": ChildActivity(unsaved=True, busy=False)}
    resp = api.post("/api/quit", headers=api.session_headers)
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["code"] == "needs_choice"
    assert detail["tools"] == ["deckifyr"]
    resp = api.post("/api/quit", json={"unsaved": "save"}, headers=api.session_headers)
    assert resp.json()["status"] == "stopping"
    assert life.stopped.wait(20)
    assert api.calls == ["listener"]


def test_quit_api_keeps_jobs_by_default(api, jobs, tmp_path):
    marker = tmp_path / "go"
    job_id = jobs.submit(gated_job(marker))
    wait_running(jobs, job_id)
    resp = api.post("/api/quit", headers=api.session_headers)
    assert resp.json() == {"status": "stopping_after_jobs", "jobs": 1, "busy": []}
    assert api.get("/api/lifecycle").json()["quit_pending"] is True
    assert api.post("/api/quit/abort", headers=api.session_headers).status_code == 200
    assert api.get("/api/lifecycle").json()["quit_pending"] is False
    marker.write_text("")
    jobs.wait(job_id, 20)


# --- end to end -------------------------------------------------------------------------


def test_idle_launcher_stops_and_clears_its_state_record(tmp_path):
    state = tmp_path / "state"
    result: dict[str, int] = {}
    config = LifecycleConfig(tick_s=0.1, override_idle=True, idle_timeout_s=1.0)
    thread = threading.Thread(
        target=lambda: result.setdefault(
            "rc", cli.run_controller(state=state, open_browser=False, lifecycle=config)
        ),
        daemon=True,
    )
    thread.start()
    deadline = time.monotonic() + 30
    while read_record(state) is None:
        assert time.monotonic() < deadline, "the launcher never wrote its record"
        time.sleep(0.05)
    record = read_record(state)
    while not instance.probe(record["port"], record["instance_id"]):
        assert time.monotonic() < deadline, "the launcher never answered /health"
        time.sleep(0.05)

    thread.join(30)
    assert not thread.is_alive(), "the idle launcher did not stop"
    assert result["rc"] == 0
    assert read_record(state) is None
    assert not instance.probe(record["port"], record["instance_id"])
