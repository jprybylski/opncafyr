"""Shared pieces of the #16 proxy harness: a real controller and request recording."""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import psutil
import uvicorn

from opncafyr.cli import _bind_loopback
from opncafyr.core.app import create_app
from opncafyr.core.bootstrap import mint_open_token, open_url
from opncafyr.core.registry import load_descriptors, load_web_adapters
from opncafyr.core.runtime import RUNTIME_FILE_ENV, Runtime, load_runtime
from opncafyr.core.security import SecurityConfig
from opncafyr.core.supervisor import DEFAULT_MAX_BODY_BYTES, Supervisor, WebAppConfig

EVIL = "https://evil.example"


def harness_runtime() -> Runtime | None:
    path = os.environ.get(RUNTIME_FILE_ENV)
    return load_runtime(path) if path else None


@dataclass
class Controller:
    """``opncafyr launch`` in a thread, with a supervisor rooted in a temp folder."""

    root: Path
    runtime: Runtime
    security: SecurityConfig = field(init=False)
    supervisor: Supervisor = field(init=False)

    def __post_init__(self) -> None:
        sock = _bind_loopback(0)
        self._sock = sock
        self.security = SecurityConfig(port=sock.getsockname()[1])
        tools = load_descriptors()
        apps = {
            tool: WebAppConfig(
                adapter=adapter,
                route=tools[tool].web_route or f"/apps/{tool}/",
                max_body_bytes=tools[tool].max_body_bytes or DEFAULT_MAX_BODY_BYTES,
            )
            for tool, adapter in load_web_adapters(tools).items()
        }
        self.supervisor = Supervisor(
            apps,
            runtime=self.runtime,
            state_root=self.root / "state",
            log_root=self.root / "logs",
            ready_timeout=120.0,
        )
        self.tokens = self.root / "state" / "open-tokens"
        app = create_app(self.security, supervisor=self.supervisor, open_tokens=self.tokens)
        self._server = uvicorn.Server(uvicorn.Config(app, log_level="warning"))
        self._thread = threading.Thread(
            target=self._server.run, kwargs={"sockets": [sock]}, daemon=True
        )
        self._thread.start()
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            try:
                if httpx.get(self.origin + "/health", timeout=1).status_code == 200:
                    return
            except httpx.HTTPError:
                time.sleep(0.1)
        raise RuntimeError("controller did not become healthy")

    @property
    def origin(self) -> str:
        return f"http://127.0.0.1:{self.security.port}"

    def client(self) -> httpx.Client:
        """A same-origin browser stand-in: session cookie and our Origin on every request."""
        cookie = f"{self.security.cookie_name}={self.security.secret}"
        return httpx.Client(
            base_url=self.origin,
            headers={"cookie": cookie, "origin": self.origin, "sec-fetch-site": "same-origin"},
            timeout=120,
        )

    def close(self) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=60)
        self._sock.close()


@dataclass
class PageRecord:
    requests: list[str] = field(default_factory=list)
    responses: list[tuple[int, str]] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)


def load_page(browser, controller: Controller, route: str, settle_ms: int = 1500) -> PageRecord:
    """Open the launcher with a one-time link (which sets the cookie), then the app; record
    every request."""
    record = PageRecord()
    context = browser.new_context()
    page = context.new_page()
    try:
        page.goto(open_url(controller.origin + "/", mint_open_token(controller.tokens)))
        page.on("request", lambda r: record.requests.append(r.url))
        page.on("response", lambda r: record.responses.append((r.status, r.url)))
        page.on("requestfailed", lambda r: record.failures.append(r.url))
        page.goto(controller.origin + route, wait_until="networkidle")
        page.wait_for_timeout(settle_ms)
    finally:
        context.close()
    return record


def assert_stays_under(record: PageRecord, controller: Controller, route: str) -> None:
    prefix = controller.origin + route
    assert record.requests, "the page made no requests"
    escaped = [u for u in record.requests if not u.startswith(prefix)]
    assert not escaped, f"requests escaped {route}: {escaped}"
    bad = [(s, u) for s, u in record.responses if s >= 400]
    assert not bad, f"failed responses: {bad}"
    assert not record.failures, f"failed requests: {record.failures}"


def tree_pids(pid: int) -> list[psutil.Process]:
    try:
        root = psutil.Process(pid)
        return [root, *root.children(recursive=True)]
    except psutil.Error:
        return []


def wait_gone(procs: list[psutil.Process], timeout: float = 15.0) -> list[psutil.Process]:
    """The processes from ``procs`` still alive (not zombies) after ``timeout``."""
    deadline = time.monotonic() + timeout
    while True:
        alive = []
        for p in procs:
            try:
                if p.is_running() and p.status() != psutil.STATUS_ZOMBIE:
                    alive.append(p)
            except psutil.Error:
                pass
        if not alive or time.monotonic() > deadline:
            return alive
        time.sleep(0.2)
