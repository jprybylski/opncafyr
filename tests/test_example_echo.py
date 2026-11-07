"""#41: the example_echo toy adapter is a whole second tool, and core/ doesn't know it.

The toy is found by the registry's package scan, shows a tile through ``/api/tools``,
runs its job through the job manager and serves its web app under
``/apps/example_echo/`` through the proxy. None of that needed a change under
``src/opncafyr/core/`` or to the workspace schema: no file outside the toy's own folder
names it.
"""

from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from opncafyr.adapters import example_echo
from opncafyr.core.app import build_supervisor, create_app
from opncafyr.core.health import HealthState
from opncafyr.core.jobmanager import JobManager
from opncafyr.core.jobs import JobState
from opncafyr.core.registry import load_descriptors
from opncafyr.core.runtime import Runtime
from opncafyr.core.security import SESSION_HEADER, SecurityConfig
from opncafyr.core.supervisor import ChildState

SRC = Path(__file__).resolve().parents[1] / "src" / "opncafyr"
TOY = SRC / "adapters" / "example_echo"
FIXTURE = Path(__file__).parent / "fixtures" / "two_analyte.csv"
PY = Runtime(executables={"python": Path(sys.executable)})
PORT = 54331


@pytest.fixture
def app_root(tmp_path, monkeypatch):
    monkeypatch.setenv("OPNCAFYR_APP_ROOT", str(tmp_path / "app"))
    return tmp_path / "app"


@pytest.fixture
def study(tmp_path):
    root = tmp_path / "Study"
    (root / ".opncafyr").mkdir(parents=True)
    (root / "source-data").mkdir()
    shutil.copyfile(FIXTURE, root / "source-data" / "pk.csv")
    return root


def _wait_health(app, tool, timeout=30.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = app.state.health.status(tool)
        if status.state != HealthState.CHECKING:
            return status
        time.sleep(0.05)
    raise AssertionError("health check never finished")


def test_registry_finds_the_toy_by_scanning_the_adapters_package():
    tool = load_descriptors()["example_echo"]
    assert tool.web_route == "/apps/example_echo/"
    assert tool.capabilities == ("summarize_csv", "echo_app")
    assert tool.executables == ("python",)


def test_tile_is_listed_with_its_health(app_root):
    security = SecurityConfig(port=PORT)
    app = create_app(security, runtime=PY)
    with TestClient(
        app,
        base_url=f"http://127.0.0.1:{PORT}",
        cookies={security.cookie_name: security.secret},
    ) as api:
        _wait_health(app, "example_echo")
        tools = {t["id"]: t for t in api.get("/api/tools").json()["tools"]}
    tile = tools["example_echo"]
    assert tile["display_name"] == "Example echo"
    assert tile["web_route"] == "/apps/example_echo/"
    assert tile["health"]["state"] == "ok"


def test_summarize_job_writes_counts_and_never_a_cell_value(study):
    jobs = JobManager(runtime=PY, poll_interval=0.05)
    jobs.open(study)
    try:
        job_id = jobs.submit(example_echo.summarize_request("source-data/pk.csv", "summaries/pk"))
        assert jobs.wait(job_id, timeout=60) == JobState.COMPLETED, jobs.get(job_id)
    finally:
        jobs.shutdown(timeout=20)

    out = study / "summaries" / "pk" / "summary.json"
    assert example_echo.check_summary(out) == []
    doc = json.loads(out.read_text("utf-8"))
    lines = FIXTURE.read_text("utf-8").splitlines()
    header = lines[0].split(",")
    assert [c["name"] for c in doc["columns"]] == header
    assert doc["rows"] == len(lines) - 1
    # Column names and counts only: no subject id or concentration from the rows.
    text = out.read_text("utf-8")
    for value in ("TA-001", "TA-010", "124.137"):
        assert value not in text


def test_summarize_job_refuses_a_path_outside_the_study(study):
    jobs = JobManager(runtime=PY, poll_interval=0.05)
    jobs.open(study)
    try:
        job_id = jobs.submit(example_echo.summarize_request("../outside.csv", "summaries/x"))
        assert jobs.wait(job_id, timeout=30) == JobState.FAILED
        assert jobs.get(job_id)["message"] == (
            "Summarize outside.csv: the CSV must be a file inside the study folder."
        )
    finally:
        jobs.shutdown(timeout=20)
    assert not (study / "summaries" / "x").exists()


def test_summarize_job_explains_an_unreadable_csv(study):
    (study / "source-data" / "bad.csv").write_bytes(b"\xff\xfe\x00bad")
    jobs = JobManager(runtime=PY, poll_interval=0.05)
    jobs.open(study)
    try:
        job_id = jobs.submit(example_echo.summarize_request("source-data/bad.csv", "summaries/b"))
        assert jobs.wait(job_id, timeout=30) == JobState.FAILED
        assert jobs.get(job_id)["message"] == "The CSV could not be read."
    finally:
        jobs.shutdown(timeout=20)


def test_web_app_runs_behind_the_proxy_with_its_token(app_root):
    tools = {"example_echo": load_descriptors()["example_echo"]}
    sup = build_supervisor(tools, PY)
    security = SecurityConfig(port=PORT)
    app = create_app(security, supervisor=sup, tools=tools, runtime=PY)
    with TestClient(
        app,
        base_url=f"http://127.0.0.1:{PORT}",
        cookies={security.cookie_name: security.secret},
    ) as api:
        api.headers[SESSION_HEADER] = security.secret
        try:
            sup.start("example_echo", {})
            apps = {a["id"]: a for a in api.get("/api/apps").json()["apps"]}
            assert apps["example_echo"] == {
                "id": "example_echo",
                "route": "/apps/example_echo/",
                "state": "running",
            }
            page = api.get("/apps/example_echo/")
            assert page.status_code == 200 and "Example echo" in page.text
            # The proxy injects the child's token; the browser never sees it.
            echo = api.post(
                "/apps/example_echo/api/echo",
                content=b"12345",
                headers={"Origin": f"http://127.0.0.1:{PORT}"},
            )
            assert echo.status_code == 200, echo.text
            assert echo.json() == {
                "method": "POST",
                "path": "/apps/example_echo/api/echo",
                "body_bytes": 5,
            }
        finally:
            sup.stop("example_echo")
    assert sup.state("example_echo") == ChildState.STOPPED


def test_web_app_refuses_requests_without_the_token(app_root):
    tools = {"example_echo": load_descriptors()["example_echo"]}
    sup = build_supervisor(tools, PY)
    try:
        child = sup.start("example_echo", {})
        base = child.context.base_url
        assert httpx.get(base + "api/health").status_code == 200
        assert httpx.get(base + "api/echo").status_code == 403
        assert (
            httpx.get(base + "api/echo", headers={"x-example-echo-token": "x"}).status_code == 403
        )
        assert httpx.get(child.context.origin + "/elsewhere").status_code == 404
    finally:
        sup.stop("example_echo")


def test_review_problems_never_quote_a_value(tmp_path):
    bad = tmp_path / "summary.json"
    bad.write_text(json.dumps({"schema_version": "1.0", "rows": "TA-001", "columns": []}))
    problems = example_echo.check_summary(bad)
    assert problems == ["summary.json at /rows fails 'type'"]


def _mentions(path: Path) -> bool:
    try:
        text = path.read_text("utf-8")
    except (UnicodeDecodeError, OSError):
        return False
    return "example_echo" in text or "example-echo" in text


def test_core_and_the_workspace_schema_never_name_the_toy():
    """Adding the toy changed nothing outside its own folder in the package."""
    offenders = [
        str(p.relative_to(SRC))
        for p in SRC.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts and TOY not in p.parents and _mentions(p)
    ]
    assert offenders == []
