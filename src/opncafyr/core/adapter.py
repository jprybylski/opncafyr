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


@dataclass(frozen=True)
class ChildActivity:
    """What a running child reports about work that stopping it could disturb."""

    unsaved: bool  # in-memory edits not yet on disk: blocks idle shutdown
    busy: bool  # work in progress inside the child (e.g. a deck build)


@runtime_checkable
class SupportsActivity(Protocol):
    """Optional for web-app adapters: report unsaved edits and busy work (#14).

    A child whose adapter lacks this is treated as never unsaved and never busy.
    """

    def activity(self, child: ChildView, client: httpx.Client) -> ChildActivity: ...


# --- Project-file editor (#52) ---------------------------------------------------------

SYNTAXES = ("md", "qmd", "rmd", "yaml", "text")
DEFAULT_MAX_EDIT_BYTES = 512 * 1024


@dataclass(frozen=True)
class EditableFile:
    """A file the editor may open: ``path`` is POSIX, relative to the project root."""

    path: str
    label: str
    syntax: str = "text"  # one of SYNTAXES; md/qmd/rmd also get the visual mode
    max_bytes: int = DEFAULT_MAX_EDIT_BYTES


@dataclass(frozen=True)
class SettingsField:
    """One field of an adapter-owned settings form. ``options`` are ``(value, label)``."""

    name: str
    label: str
    options: tuple[tuple[str, str], ...]
    help: str = ""
    required: bool = False


@dataclass(frozen=True)
class NewFileRule:
    """Where the editor may create a file, and what a new one starts as.

    ``folder`` is POSIX and project-relative ("" for the project root); core creates it if
    it is missing. The user only types a name: letters, digits, ``-`` and ``_``; core adds
    ``suffix`` and never overwrites a file.
    """

    folder: str
    suffix: str  # e.g. ".qmd"
    label: str
    syntax: str = "text"
    starter: str = ""
    max_bytes: int = DEFAULT_MAX_EDIT_BYTES


class SettingsInvalid(ValueError):
    """Settings values (or a settings file) the adapter's schema rejects."""


@runtime_checkable
class ProjectEditor(Protocol):
    """Optional for tool adapters: which project files the launcher's editor may open.

    Declared by ``project_editor`` in the descriptor and built by the adapter package's
    ``project_editor()``. Core owns the paths, hashes, locks and writes; the adapter only
    decides what is safe to show and how its settings look. The ids passed in are the
    descriptor's ``file_profile`` and ``settings_schema``.
    """

    profiles: frozenset[str]
    settings_schemas: frozenset[str]

    def title(self, file_profile: str) -> str:
        """A heading for the editor page, e.g. "Report source"."""
        ...

    def files(self, project: Path, file_profile: str) -> list[EditableFile]:
        """The files to offer, in display order. Core re-checks every path."""
        ...

    def check_text(self, file_profile: str, path: str, text: str) -> list[str]:
        """Advisory warnings for text about to be saved (never blocks a save)."""
        ...

    def settings_file(self, settings_schema: str) -> str:
        """The project-relative JSON file holding the settings (adapter-owned)."""
        ...

    def settings_form(self, settings_schema: str) -> list[SettingsField]: ...

    def settings_values(self, settings_schema: str, document: dict[str, Any] | None) -> dict:
        """Form values from the saved document (``None``: not saved yet).

        Raises ``SettingsInvalid`` if the document doesn't match the schema.
        """
        ...

    def settings_document(self, settings_schema: str, values: Mapping[str, Any]) -> dict:
        """The versioned document to save. Raises ``SettingsInvalid``."""
        ...


@runtime_checkable
class SupportsNewFiles(Protocol):
    """Optional for project editors: let the user create files (#52).

    A created file must then be on the editor's own ``files()`` list.
    """

    def new_file_rules(self, file_profile: str) -> list[NewFileRule]: ...
