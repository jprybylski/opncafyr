"""The installed runtime: absolute paths of the pinned executables.

The installer (#19) writes ``<app root>/runtime/runtime.json`` (schema ``runtime-v1``).
Adapters take every executable they start from here, never from workspace files or PATH.
For development, tests and CI, ``OPNCAFYR_RUNTIME_FILE`` names a different record. That
variable belongs to the launcher's own environment and is never passed to a child.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from opncafyr.core.manifest import validate
from opncafyr.paths import app_root

RUNTIME_FILE_ENV = "OPNCAFYR_RUNTIME_FILE"
RUNTIME_SCHEMA = "runtime-v1"


class RuntimeUnavailable(LookupError):
    """Raised when an adapter needs an executable the runtime does not provide."""


@dataclass(frozen=True)
class Runtime:
    executables: Mapping[str, Path] = field(default_factory=dict)
    r_library: Path | None = None
    source: Path | None = None

    def has(self, name: str) -> bool:
        return name in self.executables

    def executable(self, name: str) -> Path:
        try:
            return self.executables[name]
        except KeyError:
            raise RuntimeUnavailable(f"The installed runtime has no {name!r}.") from None

    def require_r_library(self) -> Path:
        if self.r_library is None:
            raise RuntimeUnavailable("The installed runtime has no R library.")
        return self.r_library


def default_runtime_file(environ: Mapping[str, str] | None = None) -> Path:
    env = os.environ if environ is None else environ
    override = env.get(RUNTIME_FILE_ENV)
    return Path(override) if override else app_root() / "runtime" / "runtime.json"


def _checked(label: str, value: str, *, directory: bool) -> Path:
    path = Path(value)
    if not path.is_absolute():
        raise ValueError(f"Runtime field {label} must be an absolute path.")
    if directory and not path.is_dir():
        raise ValueError(f"Runtime field {label} is not a directory: {path}")
    if not directory and not path.is_file():
        raise ValueError(f"Runtime field {label} is not a file: {path}")
    return path


def load_runtime(path: str | Path | None = None) -> Runtime:
    """Load and check the runtime record. A missing record means an empty runtime."""
    file = Path(path) if path is not None else default_runtime_file()
    if not file.is_file():
        return Runtime(source=None)
    doc = json.loads(file.read_text("utf-8"))
    validate(doc, RUNTIME_SCHEMA)
    executables = {
        name: _checked(f"executables.{name}", value, directory=False)
        for name, value in doc["executables"].items()
    }
    r_library = doc.get("r_library")
    return Runtime(
        executables=executables,
        r_library=_checked("r_library", r_library, directory=True) if r_library else None,
        source=file,
    )
