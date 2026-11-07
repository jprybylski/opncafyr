"""Supervisor for managed child web apps (opnca-r, deckifyr, ...).

One child per tool. The supervisor knows nothing about any tool: the adapter supplies
the argv, the readiness probe and the orderly stop. The supervisor owns everything
around them:

- a fresh per-child API token, injected later by the proxy;
- a launcher-owned working directory and TMPDIR (never the study folder), deleted after
  the child stops;
- a fixed loopback port chosen here (deckifyr's Host check ignores the port with
  ``--port 0``), with a retry on a new port if the child exits before it is ready;
- an allow-listed environment (``proctree.child_env``);
- stop: the adapter's orderly stop, a grace period, then a process-tree kill.

There is no automatic restart. A crashed child stays crashed until someone asks for a
restart (the proxy's 502 page offers one).
"""

from __future__ import annotations

import logging
import secrets
import shutil
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import IO, Any

import httpx

from opncafyr.core.adapter import (
    ChildActivity,
    LaunchContext,
    LaunchSpec,
    SupportsActivity,
    WebAppAdapter,
)
from opncafyr.core.proctree import (
    OwnedProcess,
    child_env,
    child_tmpdir,
    free_loopback_port,
    spawn,
)
from opncafyr.core.redaction import REDACTOR, Redactor
from opncafyr.core.runtime import Runtime

log = logging.getLogger(__name__)

DEFAULT_MAX_BODY_BYTES = 10 * 1024 * 1024


class ChildState(StrEnum):
    NOT_STARTED = "not_started"
    STARTING = "starting"
    RUNNING = "running"
    CRASHED = "crashed"
    STOPPED = "stopped"


class LaunchError(RuntimeError):
    """A child could not be started or did not become ready."""


@dataclass(frozen=True)
class WebAppConfig:
    """Per-tool settings taken from the trusted descriptor."""

    adapter: WebAppAdapter
    route: str  # "/apps/<tool>/"
    max_body_bytes: int = DEFAULT_MAX_BODY_BYTES


@dataclass
class Child:
    tool: str
    context: LaunchContext
    spec: LaunchSpec
    proc: OwnedProcess
    log_path: Path
    max_body_bytes: int
    state: ChildState = ChildState.STARTING
    ready_line: str | None = None
    exit_code: int | None = None
    started_at: float = field(default_factory=time.time)
    extra_tmp: Path | None = None  # a space-free TMPDIR outside workdir (child_tmpdir)
    _reader: threading.Thread | None = field(default=None, repr=False)

    @property
    def pid(self) -> int:
        return self.proc.pid

    @property
    def alive(self) -> bool:
        return self.proc.poll() is None


class Supervisor:
    def __init__(
        self,
        apps: Mapping[str, WebAppConfig],
        *,
        runtime: Runtime,
        state_root: Path,
        log_root: Path,
        ready_timeout: float = 60.0,
        stop_grace: float = 10.0,
        port_attempts: int = 3,
        port_picker: Callable[[], int] = free_loopback_port,
        redactor: Redactor = REDACTOR,
    ) -> None:
        self.apps = dict(apps)
        self.redactor = redactor
        self.runtime = runtime
        self.state_root = Path(state_root)
        self.log_root = Path(log_root)
        self.ready_timeout = ready_timeout
        self.stop_grace = stop_grace
        self.port_attempts = port_attempts
        self._port_picker = port_picker
        self._children: dict[str, Child] = {}
        self._sessions: dict[str, dict[str, Any]] = {}
        # One lock per tool serialises start/stop. Queries never take it, so the proxy's
        # event loop is not blocked while a child starts.
        self._tool_locks = {tool: threading.RLock() for tool in self.apps}

    # --- queries ---------------------------------------------------------------------

    @property
    def tools(self) -> list[str]:
        return sorted(self.apps)

    def get(self, tool: str) -> Child | None:
        child = self._children.get(tool)
        if child is not None and child.state is ChildState.RUNNING and not child.alive:
            child.state = ChildState.CRASHED
            child.exit_code = child.proc.poll()
            log.warning("%s exited unexpectedly (code %s)", tool, child.exit_code)
        return child

    def state(self, tool: str) -> ChildState:
        child = self.get(tool)
        return ChildState.NOT_STARTED if child is None else child.state

    def can_restart(self, tool: str) -> bool:
        return tool in self._sessions

    def activity(self) -> dict[str, ChildActivity]:
        """Unsaved/busy state of each running child whose adapter can report it.

        A probe that fails reports ``unsaved``: a child we can't ask may hold unsaved
        edits, so it must not let the launcher stop on its own.
        """
        out: dict[str, ChildActivity] = {}
        for tool in self.tools:
            child = self.get(tool)
            adapter = self.apps[tool].adapter
            if child is None or child.state is not ChildState.RUNNING:
                continue
            if not isinstance(adapter, SupportsActivity):
                continue
            try:
                with httpx.Client(timeout=2.0) as client:
                    out[tool] = adapter.activity(child, client)
            except Exception as exc:  # noqa: BLE001 - any failure means "unknown"
                log.warning("%s activity probe failed: %s", tool, type(exc).__name__)
                out[tool] = ChildActivity(unsaved=True, busy=False)
        return out

    # --- lifecycle -------------------------------------------------------------------

    def start(self, tool: str, session: Mapping[str, Any] | None = None) -> Child:
        """Start (or replace) the tool's child and wait until it is ready."""
        if tool not in self.apps:
            raise KeyError(f"Unknown web app {tool!r}.")
        session = dict(session or {})
        with self._tool_locks[tool]:
            self.stop(tool)
            self._sessions[tool] = session
            last_error = "no attempt made"
            for attempt in range(1, self.port_attempts + 1):
                child = self._spawn(tool, session)
                self._children[tool] = child
                outcome = self._wait_ready(child)
                if outcome is None:
                    child.state = ChildState.RUNNING
                    log.info("%s ready on port %s (pid %s)", tool, child.context.port, child.pid)
                    return child
                last_error = outcome
                exited_early = child.exit_code is not None  # read before teardown sets it
                self._teardown(child)
                child.state = ChildState.STOPPED
                if not exited_early:
                    break  # still running but never ready: a new port won't help
                log.warning("%s attempt %s failed: %s", tool, attempt, outcome)
            raise LaunchError(f"{tool} did not start: {last_error}")

    def restart(self, tool: str) -> Child:
        if tool not in self._sessions:
            raise LaunchError(f"{tool} has not been started in this launch.")
        return self.start(tool, self._sessions[tool])

    def stop(self, tool: str) -> None:
        if tool not in self.apps:
            return
        with self._tool_locks[tool]:
            child = self._children.get(tool)
            if child is None or child.state is ChildState.STOPPED:
                return
            # Snapshot descendants first: once the child exits they are reparented and
            # can no longer be found by walking its tree.
            known = child.proc.descendants()
            if child.alive:
                try:
                    with httpx.Client(timeout=5.0) as client:
                        self.apps[tool].adapter.stop(child, client)
                except Exception as exc:  # noqa: BLE001 - always fall through to the kill
                    log.warning("%s orderly stop failed: %s", tool, type(exc).__name__)
                try:
                    child.exit_code = child.proc.wait(self.stop_grace)
                except Exception:  # noqa: BLE001 - TimeoutExpired: kill below
                    log.warning("%s did not stop within %ss; killing", tool, self.stop_grace)
            self._teardown(child, known)
            child.state = ChildState.STOPPED

    def stop_all(self) -> None:
        for tool in list(self._children):
            self.stop(tool)

    # --- internals -------------------------------------------------------------------

    def _spawn(self, tool: str, session: Mapping[str, Any]) -> Child:
        app = self.apps[tool]
        name = f"{tool}-{uuid.uuid4().hex[:12]}"
        workdir = self.state_root / "children" / name
        workdir.mkdir(parents=True)
        # The macOS app root has a space, and R won't start with such a TMPDIR.
        tmpdir, extra_tmp = child_tmpdir(workdir / "tmp", f"opncafyr-{name}-")
        ctx = LaunchContext(
            tool=tool,
            port=self._port_picker(),
            base_path=app.route,
            workdir=workdir,
            tmpdir=tmpdir,
            runtime=self.runtime,
            token=secrets.token_urlsafe(32),
        )
        try:
            spec = app.adapter.launch(ctx, session)
        except Exception:
            _remove_dirs(workdir, extra_tmp)
            raise
        tmp = str(tmpdir)
        env = child_env({**spec.env, "TMPDIR": tmp, "TMP": tmp, "TEMP": tmp}, spec.path_dirs)
        self.log_root.mkdir(parents=True, exist_ok=True)
        log_path = self.log_root / f"{tool}.log"
        try:
            proc = spawn(spec.argv, env=env, cwd=workdir)
        except Exception:
            _remove_dirs(workdir, extra_tmp)
            raise
        child = Child(
            tool=tool,
            context=ctx,
            spec=spec,
            proc=proc,
            log_path=log_path,
            max_body_bytes=app.max_body_bytes,
            extra_tmp=extra_tmp,
        )
        reader = threading.Thread(
            target=_pump_output,
            args=(child, self.redactor),
            name=f"{tool}-output",
            daemon=True,
        )
        child._reader = reader
        reader.start()
        return child

    def _wait_ready(self, child: Child) -> str | None:
        """None when ready; otherwise why not (and ``exit_code`` if it exited)."""
        adapter = self.apps[child.tool].adapter
        deadline = time.monotonic() + self.ready_timeout
        with httpx.Client(timeout=2.0) as client:
            while time.monotonic() < deadline:
                code = child.proc.poll()
                if code is not None:
                    child.exit_code = code
                    return f"exited with code {code} before it was ready"
                needs_line = child.spec.ready_line_prefix is not None
                if not needs_line or child.ready_line is not None:
                    try:
                        if adapter.is_ready(child, client):
                            return None
                    except httpx.HTTPError:
                        pass
                    except LaunchError as exc:  # the adapter knows it will never be ready
                        return str(exc)
                time.sleep(0.1)
        return f"not ready after {self.ready_timeout:.0f}s"

    def _teardown(self, child: Child, known: list[Any] | None = None) -> None:
        child.proc.kill_tree(known=known or ())
        if child.exit_code is None:
            child.exit_code = child.proc.poll()
        if child._reader is not None:
            child._reader.join(timeout=5)
        for _ in range(5):  # Windows may hold files briefly after a kill
            _remove_dirs(child.context.workdir, child.extra_tmp)
            if not child.context.workdir.exists():
                break
            time.sleep(0.2)


def _remove_dirs(*dirs: Path | None) -> None:
    for d in dirs:
        if d is not None:
            shutil.rmtree(d, ignore_errors=True)


def _pump_output(child: Child, redactor: Redactor) -> None:
    """Copy the child's merged stdout/stderr to its log, redacted, and catch the ready line.

    The ready line is read from the raw output; only the log file sees redacted text.
    """
    stream: IO[bytes] | None = child.proc.popen.stdout
    if stream is None:
        return
    prefix = child.spec.ready_line_prefix
    with child.log_path.open("ab") as out:
        out.write(f"--- start {child.tool} pid {child.pid} port {child.context.port}\n".encode())
        for raw in iter(stream.readline, b""):
            out.write(redactor.redact_bytes(raw))
            out.flush()
            if prefix and child.ready_line is None:
                line = raw.decode("utf-8", "replace").strip()
                if line.startswith(prefix):
                    child.ready_line = line
    stream.close()
