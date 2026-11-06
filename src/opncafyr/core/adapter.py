"""The adapter contract every tool implements.

The controller knows capabilities and file contracts, not package-specific screens or
calculation logic (docs/spec.md, "Stable adapters make delivery and reuse testable").
Adapters translate these calls into absolute-path process invocations or narrowly
scoped loopback HTTP calls. Workspace files supply data and options only.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from opncafyr.core.jobs import JobState
from opncafyr.core.runtime import Runtime

if TYPE_CHECKING:
    import httpx


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Artifact:
    path: Path
    kind: str
    media_type: str
    sha256: str


@dataclass
class JobHandle:
    job_id: str
    capability: str
    state: JobState = JobState.QUEUED
    message: str = ""


@runtime_checkable
class Adapter(Protocol):
    """Implemented by each tool adapter under ``opncafyr.adapters``."""

    id: str

    def prepare(self, workspace: Path) -> None: ...

    def validate(self, capability: str, job_input: dict[str, Any]) -> ValidationResult: ...

    def run(self, capability: str, job_input: dict[str, Any]) -> JobHandle: ...

    def status(self, job: JobHandle) -> JobHandle: ...

    def cancel(self, job: JobHandle) -> JobHandle: ...

    def artifacts(self, job: JobHandle) -> list[Artifact]: ...

    def shutdown(self) -> None: ...


# --- Managed web apps (children served under /apps/<tool>/) --------------------------


@dataclass(frozen=True)
class LaunchContext:
    """What the supervisor gives an adapter to build one child's command line."""

    tool: str
    port: int
    base_path: str  # the descriptor route, e.g. "/apps/opnca/"
    workdir: Path  # launcher-owned cwd; never a study folder
    tmpdir: Path  # app-owned TMPDIR/TMP/TEMP, deleted after stop
    runtime: Runtime
    token: str  # per-child API token, injected by the proxy

    @property
    def origin(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def base_url(self) -> str:
        return self.origin + self.base_path


@dataclass(frozen=True)
class LaunchSpec:
    """How to start a child. Executables come from ``LaunchContext.runtime`` only."""

    argv: list[str]
    env: dict[str, str] = field(default_factory=dict)
    path_dirs: list[Path] = field(default_factory=list)
    # Headers the proxy adds to every forwarded request (and strips from inbound ones).
    auth_headers: dict[str, str] = field(default_factory=dict)
    # A stdout line starting with this marks the child as listening.
    ready_line_prefix: str | None = None


class ChildView(Protocol):
    """The parts of a running child an adapter may look at."""

    @property
    def context(self) -> LaunchContext: ...

    @property
    def spec(self) -> LaunchSpec: ...

    @property
    def ready_line(self) -> str | None: ...

    @property
    def pid(self) -> int: ...


@runtime_checkable
class WebAppAdapter(Protocol):
    """Start, readiness and orderly stop for a tool's web app.

    ``session`` holds launcher-approved values (absolute paths already resolved with
    ``resolve_in_root``, run ids). It never names an executable or adds arguments
    verbatim.
    """

    def launch(self, ctx: LaunchContext, session: Mapping[str, Any]) -> LaunchSpec: ...

    def is_ready(self, child: ChildView, client: httpx.Client) -> bool: ...

    def stop(self, child: ChildView, client: httpx.Client) -> None: ...
