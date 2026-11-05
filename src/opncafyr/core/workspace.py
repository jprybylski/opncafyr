"""Workspace path canonicalization.

Every file path the UI or an adapter asks for is resolved under the selected workspace
root. Absolute paths, drive or UNC prefixes, ``..`` escapes and symlinks that leave the
root are rejected. Ported in spirit from makecoolplots' ``resolve_in_root``
(crates/mcp-server/src/sandbox.rs).
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath, PureWindowsPath


class WorkspaceAccessError(ValueError):
    """Raised when a requested path is outside the approved workspace."""


def resolve_in_root(root: str | Path, requested: str | Path) -> Path:
    """Return the canonical absolute path of ``requested`` inside ``root``.

    ``requested`` must be relative. The result may not exist yet (for outputs), but its
    canonical form, after following any existing symlinks, must stay under ``root``.
    """
    root_path = Path(root).resolve(strict=True)
    if not root_path.is_dir():
        raise WorkspaceAccessError("Workspace root is not a directory.")

    text = str(requested)
    if not text or "\x00" in text:
        raise WorkspaceAccessError("Empty or invalid path.")

    # Reject absolute paths in either flavour regardless of the host OS.
    win = PureWindowsPath(text)
    if PurePosixPath(text).is_absolute() or win.is_absolute() or win.drive or win.root:
        raise WorkspaceAccessError("Absolute paths are not allowed.")

    candidate = (root_path / text).resolve(strict=False)
    if not candidate.is_relative_to(root_path):
        raise WorkspaceAccessError("Path escapes the workspace.")
    return candidate
