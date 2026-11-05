"""The adapter contract every tool implements.

The controller knows capabilities and file contracts, not package-specific screens or
calculation logic (docs/spec.md, "Stable adapters make delivery and reuse testable").
Adapters translate these calls into absolute-path process invocations or narrowly
scoped loopback HTTP calls. Workspace files supply data and options only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from opncafyr.core.jobs import JobState


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
