"""Startup health probes for the installed tools (#13).

Each descriptor names a probe kind, a runtime executable and a target. The launcher
builds the command itself, so a descriptor never supplies code to run verbatim:

- ``python_import``: ``<python> -I -c "import <module>"``
- ``r_package``: ``<Rscript> --vanilla -e "library(<package>)"`` with ``R_LIBS`` set to
  the runtime's R library
- ``version_flag``: ``<executable> --version``

Probes run once, in the background, when the controller starts, through
``proctree.spawn`` (argv only, allow-listed environment, launcher-owned working folder).
Their output is discarded; a status carries only a short reason. A missing optional
executable (``optional_requirements``) leaves the tool usable and reports the features
it would enable as unavailable.
"""

from __future__ import annotations

import logging
import tempfile
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from opncafyr.core.proctree import DEVNULL, OwnedProcess, TimeoutExpired, child_env, spawn
from opncafyr.core.registry import OptionalRequirement, ToolDescriptor
from opncafyr.core.runtime import Runtime, RuntimeUnavailable

log = logging.getLogger(__name__)

PROBE_TIMEOUT_S = 60.0


class HealthState(StrEnum):
    CHECKING = "checking"
    OK = "ok"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class ToolStatus:
    state: HealthState
    detail: str
    missing: tuple[OptionalRequirement, ...] = ()

    def as_json(self) -> dict[str, Any]:
        return {
            "health": {"state": str(self.state), "detail": self.detail},
            "missing_requirements": [
                {"name": r.name, "message": r.message, "capabilities": list(r.capabilities)}
                for r in self.missing
            ],
        }


def missing_requirements(tool: ToolDescriptor, runtime: Runtime) -> tuple[OptionalRequirement, ...]:
    return tuple(r for r in tool.optional_requirements if not runtime.has(r.executable))


def probe_command(tool: ToolDescriptor, runtime: Runtime) -> tuple[list[str], dict[str, str]]:
    """The probe's argv and extra environment. Raises ``RuntimeUnavailable``."""
    probe = tool.health
    if probe is None:
        raise RuntimeUnavailable(f"{tool.id} has no health probe.")
    exe = str(runtime.executable(probe.executable))
    if probe.kind == "python_import":
        return [exe, "-I", "-c", f"import {probe.target}"], {}
    if probe.kind == "r_package":
        library = runtime.require_r_library()
        return [exe, "--vanilla", "-e", f"library({probe.target})"], {"R_LIBS": str(library)}
    if probe.kind == "version_flag":
        return [exe, "--version"], {}
    raise ValueError(f"Unknown probe kind {probe.kind!r}.")


def check_tool(
    tool: ToolDescriptor,
    runtime: Runtime,
    *,
    workdir: Path,
    timeout: float = PROBE_TIMEOUT_S,
    live: dict[int, OwnedProcess] | None = None,
) -> ToolStatus:
    """Run one tool's probe and return its status."""
    missing = missing_requirements(tool, runtime)
    absent = [name for name in tool.executables if not runtime.has(name)]
    if absent:
        names = ", ".join(repr(n) for n in absent)
        return ToolStatus(
            HealthState.UNAVAILABLE, f"The installed runtime has no {names}.", missing
        )
    try:
        argv, extra = probe_command(tool, runtime)
    except RuntimeUnavailable as exc:
        return ToolStatus(HealthState.UNAVAILABLE, str(exc), missing)
    env = child_env(extra, path_dirs=[Path(argv[0]).parent])
    try:
        proc = spawn(argv, env=env, cwd=workdir, stdout=DEVNULL, stderr=DEVNULL)
    except OSError as exc:
        detail = f"The health check could not start ({type(exc).__name__})."
        return ToolStatus(HealthState.UNAVAILABLE, detail, missing)
    if live is not None:
        live[proc.pid] = proc
    try:
        code = proc.wait(timeout)
    except TimeoutExpired:
        proc.kill_tree()
        detail = f"The health check did not finish within {timeout:g} seconds."
        return ToolStatus(HealthState.UNAVAILABLE, detail, missing)
    finally:
        if live is not None:
            live.pop(proc.pid, None)
    if code != 0:
        detail = f"The health check failed (exit code {code})."
        return ToolStatus(HealthState.UNAVAILABLE, detail, missing)
    return ToolStatus(HealthState.OK, "Health check passed.", missing)


class ToolHealth:
    """Probe every installed tool once, in the background, and keep the results."""

    def __init__(
        self,
        tools: Mapping[str, ToolDescriptor],
        runtime: Runtime,
        *,
        timeout: float = PROBE_TIMEOUT_S,
    ) -> None:
        self.tools = dict(tools)
        self.runtime = runtime
        self.timeout = timeout
        self._lock = threading.Lock()
        self._live: dict[int, OwnedProcess] = {}
        self._stopping = threading.Event()
        self._thread: threading.Thread | None = None
        self._status = {
            tool_id: ToolStatus(
                HealthState.CHECKING, "Checking…", missing_requirements(tool, runtime)
            )
            for tool_id, tool in self.tools.items()
        }

    def status(self, tool_id: str) -> ToolStatus:
        with self._lock:
            return self._status[tool_id]

    def start(self) -> None:
        self._thread = threading.Thread(target=self.check_all, name="tool-health", daemon=True)
        self._thread.start()

    def check_all(self) -> None:
        with tempfile.TemporaryDirectory(prefix="opncafyr-health-") as workdir:
            for tool_id, tool in self.tools.items():
                if self._stopping.is_set():
                    return
                try:
                    status = check_tool(
                        tool,
                        self.runtime,
                        workdir=Path(workdir),
                        timeout=self.timeout,
                        live=self._live,
                    )
                except Exception as exc:  # noqa: BLE001 - report, never leave "checking"
                    log.exception("tool %s health check raised", tool_id)
                    status = ToolStatus(
                        HealthState.UNAVAILABLE,
                        f"The health check raised {type(exc).__name__}.",
                        missing_requirements(tool, self.runtime),
                    )
                with self._lock:
                    self._status[tool_id] = status
                log.info("tool %s health: %s", tool_id, status.state)

    def wait(self, timeout: float | None = None) -> bool:
        if self._thread is not None:
            self._thread.join(timeout)
            return not self._thread.is_alive()
        return True

    def stop(self) -> None:
        self._stopping.set()
        for proc in list(self._live.values()):
            proc.kill_tree()
        self.wait(10)
