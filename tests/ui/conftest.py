"""A real controller on a loopback port with stub adapters, and a headless Chromium.

Run with ``uv sync --group ui``, ``uv run playwright install chromium`` and
``uv run pytest -m ui tests/ui``. No R, no child apps: the only web app is the toy child
from ``tests/toy_child.py``.
"""

from __future__ import annotations

import shutil
import socket
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest
import uvicorn
from home_seed import BROKEN_RUN_ID, JOB_LABEL, RUN_ID

from artifact_docs import publication, run_manifest, write_publication, write_run
from opncafyr.core.app import create_app
from opncafyr.core.bootstrap import mint_open_token, open_url
from opncafyr.core.jobmanager import CommandSpec, JobManager, JobRequest
from opncafyr.core.lifecycle import LifecycleConfig, Settings
from opncafyr.core.security import SESSION_HEADER, SecurityConfig
from opncafyr.core.workspace import RecentWorkspaces
from toy_adapter import toy_supervisor

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"

SLEEP = JobRequest(
    kind="toy.sleep",
    label=JOB_LABEL,
    timeout_s=600,
    build=lambda ctx: CommandSpec([sys.executable, "-c", "import time; time.sleep(600)"]),
)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@dataclass
class Home:
    url: str
    jobs: JobManager
    job_id: str
    quits: list[str]
    tokens: Path
    study: Path
    app_root: Path

    def open_url(self) -> str:
        """A fresh one-time link, as the launcher would open it."""
        return open_url(self.url, mint_open_token(self.tokens))


@pytest.fixture(scope="session")
def browser():
    sync_api = pytest.importorskip("playwright.sync_api")
    with sync_api.sync_playwright() as pw:
        b = pw.chromium.launch()
        yield b
        b.close()


@pytest.fixture
def home(tmp_path: Path, monkeypatch):
    # The controller runs in this process: keep its app files (support bundles) in tmp.
    monkeypatch.setenv("OPNCAFYR_APP_ROOT", str(tmp_path / "app root"))
    study = tmp_path / "Study A"
    (study / "source-data").mkdir(parents=True)
    shutil.copyfile(FIXTURES / "two_analyte.csv", study / "source-data" / "two_analyte.csv")
    port = _free_port()
    security = SecurityConfig(port=port)
    sup, _ = toy_supervisor(tmp_path)
    jobs = JobManager(poll_interval=0.05)
    quits: list[str] = []
    app = create_app(
        security,
        request_shutdown=lambda: quits.append("quit"),
        supervisor=sup,
        jobs=jobs,
        picker=lambda prompt, start: study,
        recent=RecentWorkspaces(tmp_path / "state" / "recent.json"),
        settings=Settings(tmp_path / "state" / "settings.json"),
        lifecycle=LifecycleConfig(),
        open_tokens=tmp_path / "state" / "open-tokens",
    )
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="on")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 20
    while not server.started:
        assert time.monotonic() < deadline, "controller did not start"
        time.sleep(0.05)

    base = f"http://127.0.0.1:{port}"
    headers = {SESSION_HEADER: security.secret, "origin": base}
    cookies = {security.cookie_name: security.secret}
    with httpx.Client(base_url=base, headers=headers, cookies=cookies) as c:
        folder = c.post("/api/workspace/pick").json()["folder"]
        c.post("/api/workspace/init", json={"path": folder["path"]}).raise_for_status()
    write_run(study, run_manifest(RUN_ID))
    write_run(study, None, run_id=BROKEN_RUN_ID)
    write_publication(study, publication())
    sup.start("toy")
    job_id = jobs.submit(SLEEP)
    deadline = time.monotonic() + 20
    while jobs.get(job_id)["state"] != "running":
        assert time.monotonic() < deadline, "job did not start"
        time.sleep(0.05)

    try:
        yield Home(
            url=f"{base}/",
            jobs=jobs,
            job_id=job_id,
            quits=quits,
            tokens=tmp_path / "state" / "open-tokens",
            study=study,
            app_root=tmp_path / "app root",
        )
    finally:
        server.should_exit = True
        thread.join(30)


@pytest.fixture(params=["light", "dark"])
def page(request, browser, home):
    context = browser.new_context(color_scheme=request.param)
    pg = context.new_page()
    pg.errors = []
    pg.on("pageerror", lambda exc: pg.errors.append(str(exc)))
    pg.on("console", lambda msg: msg.type == "error" and pg.errors.append(msg.text))
    pg.goto(home.open_url())
    # Wait until every panel has loaded its data.
    pg.get_by_role("link", name="Open toy").wait_for()
    pg.get_by_role("button", name=f"Cancel {JOB_LABEL}").wait_for()
    pg.locator("#runs-table").get_by_text(RUN_ID).wait_for()
    yield pg
    context.close()
