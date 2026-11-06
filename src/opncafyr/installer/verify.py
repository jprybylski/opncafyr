"""SHA-256 checks against the pins, and the errors that name the component at fault."""

from __future__ import annotations

import hashlib
from pathlib import Path


class InstallError(RuntimeError):
    """A failure the installer reports to the user (no traceback)."""


class AssetError(InstallError):
    def __init__(self, component: str, file: str, problem: str) -> None:
        super().__init__(f"{component}: {file}: {problem}")
        self.component = component
        self.file = file
        self.problem = problem


class MissingAssets(InstallError):
    """One or more pinned files could not be found (or downloaded)."""

    def __init__(self, errors: list[AssetError]) -> None:
        components = sorted({e.component for e in errors})
        lines = [f"  - {e}" for e in errors[:20]]
        if len(errors) > 20:
            lines.append(f"  … and {len(errors) - 20} more")
        super().__init__(
            f"{len(errors)} pinned file(s) missing or wrong, in: {', '.join(components)}\n"
            + "\n".join(lines)
        )
        self.errors = errors
        self.components = components


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
