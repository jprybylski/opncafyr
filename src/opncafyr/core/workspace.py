"""Study workspaces: path canonicalization, inspection, initialization and opening.

Every file path the UI or an adapter asks for is resolved under the selected workspace
root with ``resolve_in_root``. Absolute paths, drive or UNC prefixes, ``..`` escapes and
symlinks that leave the root are rejected. Ported in spirit from makecoolplots'
``resolve_in_root`` (crates/mcp-server/src/sandbox.rs).

A folder becomes a workspace when ``.opncafyr/workspace.json`` exists (#17).
Initializing a folder creates only ``.opncafyr/``; nothing else in the folder is read
beyond a shallow entry count, or written. Opening checks the schema version: an older
workspace is upgraded only when the user agrees (``core.migrations``), and a newer one
is refused.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import threading
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from jsonschema import ValidationError

from opncafyr.core import migrations
from opncafyr.core.manifest import atomic_write_json, validate
from opncafyr.paths import WORKSPACE_META_DIR

log = logging.getLogger(__name__)

WORKSPACE_FILE = migrations.WORKSPACE_FILE
WORKSPACE_SCHEMA = migrations.WORKSPACE_SCHEMA
RECENT_SCHEMA = "recent-workspaces-v1"
RECENT_FILE = "recent-workspaces.json"
RECENT_MAX = 10


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


# --- workspace metadata ----------------------------------------------------------------


class WorkspaceError(RuntimeError):
    """A folder can't be initialized or opened. ``message`` is written for a person."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


# Folder states reported by ``inspect_folder``.
NEW = "new"  # a folder with no .opncafyr/: can be initialized
WORKSPACE = "workspace"  # current schema: can be opened
NEEDS_MIGRATION = "needs_migration"  # older schema: opened after an upgrade
NEWER = "newer"  # made by a newer opncafyr: refused
INCOMPLETE = "incomplete"  # .opncafyr/ without workspace.json: can be initialized
INVALID = "invalid"  # unreadable or damaged metadata: refused
MISSING = "missing"  # not an existing folder
UNREADABLE = "unreadable"  # exists, but this user may not open it


@dataclass(frozen=True)
class FolderInfo:
    path: str
    name: str
    state: str
    message: str
    writable: bool = False
    entries: int = 0
    schema_version: str | None = None
    app_version: str | None = None
    enclosing_workspace: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Workspace:
    root: Path
    document: dict[str, Any]
    migrated_from: str | None = None
    backups: tuple[Path, ...] = ()

    def summary(self) -> dict[str, Any]:
        return {
            "path": str(self.root),
            "name": self.root.name,
            "schema_version": self.document["schema_version"],
            "created_at": self.document["created_at"],
            "app_version": self.document.get("app_version"),
        }


def _enclosing_workspace(root: Path) -> Path | None:
    for parent in root.parents:
        if (parent / WORKSPACE_META_DIR / WORKSPACE_FILE).is_file():
            return parent
    return None


def _entries(root: Path) -> int:
    try:
        with os.scandir(root) as it:
            return sum(1 for e in it if e.name != WORKSPACE_META_DIR)
    except OSError:
        return 0


def _read_document(meta: Path) -> dict[str, Any]:
    try:
        doc = json.loads((meta / WORKSPACE_FILE).read_text("utf-8"))
    except (OSError, ValueError):
        raise WorkspaceError(INVALID, "The workspace file can't be read.") from None
    if not isinstance(doc, dict):
        raise WorkspaceError(INVALID, "The workspace file is not a JSON object.")
    return doc


def _classify(doc: dict[str, Any]) -> tuple[str, str]:
    """Return (state, message) for a loaded workspace.json."""
    version = doc.get("schema_version")
    try:
        found = migrations.parse_version(version)
    except ValueError:
        return INVALID, "The workspace file has no readable schema version."
    current = migrations.parse_version(migrations.CURRENT_VERSION)
    if found > current:
        made_by = doc.get("app_version")
        by = f", opncafyr {made_by}" if isinstance(made_by, str) else ""
        return NEWER, (
            f"This workspace was created by a newer version of opncafyr (schema {version}"
            f"{by}). This version supports schema {migrations.CURRENT_VERSION}. Update "
            "opncafyr to open it."
        )
    if found < current:
        try:
            migrations.chain(str(version))
        except migrations.MigrationError as exc:
            return INVALID, str(exc)
        return NEEDS_MIGRATION, (
            f"This workspace uses an older format (schema {version}). Opening it upgrades "
            f"it to schema {migrations.CURRENT_VERSION}. A backup of the old file is kept in "
            f"{WORKSPACE_META_DIR}/{migrations.BACKUP_DIR}/."
        )
    try:
        validate(doc, WORKSPACE_SCHEMA)
    except ValidationError:
        return INVALID, "The workspace file does not match its schema."
    return WORKSPACE, "This folder is an opncafyr workspace."


def inspect_folder(path: str | Path) -> FolderInfo:
    """Describe ``path`` for the user before anything is created. Writes nothing."""
    raw = Path(path)
    try:
        root = raw.resolve(strict=True)
    except PermissionError:
        return FolderInfo(
            str(raw), raw.name, UNREADABLE, "You don't have permission to open this folder."
        )
    except OSError:
        return FolderInfo(str(raw), raw.name, MISSING, "This folder does not exist.")
    if not root.is_dir():
        return FolderInfo(str(root), root.name, MISSING, "This is not a folder.")

    enclosing = _enclosing_workspace(root)
    common = {
        "path": str(root),
        "name": root.name,
        "writable": os.access(root, os.W_OK),
        "entries": _entries(root),
        "enclosing_workspace": str(enclosing) if enclosing else None,
    }
    meta = root / WORKSPACE_META_DIR
    if not meta.exists():
        message = "This folder is not a workspace yet. Creating one adds only a "
        message += f"{WORKSPACE_META_DIR}/ folder; nothing else in it is changed."
        if enclosing is not None:
            message += f" It is inside another workspace ({enclosing})."
        if not common["writable"]:
            message += " You may not have permission to write here."
        return FolderInfo(state=NEW, message=message, **common)
    if not meta.is_dir():
        return FolderInfo(
            state=INVALID, message=f"{WORKSPACE_META_DIR} here is a file, not a folder.", **common
        )
    if not (meta / WORKSPACE_FILE).exists():
        return FolderInfo(
            state=INCOMPLETE,
            message=f"{WORKSPACE_META_DIR}/ exists but has no {WORKSPACE_FILE}. Creating the "
            "workspace finishes setting it up.",
            **common,
        )
    try:
        doc = _read_document(meta)
    except WorkspaceError as exc:
        return FolderInfo(state=exc.code, message=exc.message, **common)
    state, message = _classify(doc)
    version = doc.get("schema_version")
    app_version = doc.get("app_version")
    return FolderInfo(
        state=state,
        message=message,
        schema_version=version if isinstance(version, str) else None,
        app_version=app_version if isinstance(app_version, str) else None,
        **common,
    )


def init_workspace(path: str | Path, app_version: str) -> Workspace:
    """Create ``.opncafyr/workspace.json`` in an existing folder, then open it."""
    info = inspect_folder(path)
    if info.state not in (NEW, INCOMPLETE):
        if info.state in (WORKSPACE, NEEDS_MIGRATION, NEWER):
            raise WorkspaceError("exists", "This folder is already a workspace.")
        raise WorkspaceError(info.state, info.message)
    root = Path(info.path)
    meta = root / WORKSPACE_META_DIR
    created = not meta.exists()
    document = {
        "schema_version": migrations.CURRENT_VERSION,
        "created_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "app_version": app_version,
        "sources": [],
    }
    try:
        meta.mkdir(exist_ok=True)
        atomic_write_json(meta / WORKSPACE_FILE, document, WORKSPACE_SCHEMA)
    except PermissionError:
        _remove_if_empty(meta, created)
        raise WorkspaceError(
            "permission", "You don't have permission to create a workspace in this folder."
        ) from None
    except OSError:
        _remove_if_empty(meta, created)
        raise WorkspaceError("io", "The workspace could not be created here.") from None
    return open_workspace(root)


def _remove_if_empty(meta: Path, created: bool) -> None:
    if created:
        with contextlib.suppress(OSError):
            meta.rmdir()


def open_workspace(path: str | Path, allow_migrate: bool = False) -> Workspace:
    """Load a workspace, upgrading an older schema only when ``allow_migrate`` is set."""
    info = inspect_folder(path)
    if info.state == NEW:
        raise WorkspaceError("not_workspace", "This folder is not a workspace yet.")
    if info.state == INCOMPLETE:
        raise WorkspaceError("not_workspace", info.message)
    if info.state in (MISSING, UNREADABLE, INVALID, NEWER):
        raise WorkspaceError(info.state, info.message)
    root = Path(info.path)
    meta = root / WORKSPACE_META_DIR
    doc = _read_document(meta)
    state, message = _classify(doc)
    if state == WORKSPACE:
        return Workspace(root=root, document=doc)
    if state == NEEDS_MIGRATION:
        if not allow_migrate:
            raise WorkspaceError(NEEDS_MIGRATION, message)
        try:
            result = migrations.migrate(meta, doc)
        except migrations.MigrationError as exc:
            raise WorkspaceError("migration_failed", str(exc)) from None
        except OSError:
            raise WorkspaceError(
                "migration_failed", "The workspace could not be upgraded (file error)."
            ) from None
        return Workspace(
            root=root,
            document=result.document,
            migrated_from=result.from_version,
            backups=tuple(result.backups),
        )
    raise WorkspaceError(state, message)


# --- imported sources --------------------------------------------------------------------

_sources_lock = threading.Lock()


def _source_id(rel_path: str, sha256: str, taken: set[str]) -> str:
    stem = PurePosixPath(rel_path).stem
    base = re.sub(r"[^a-z0-9]+", "-", stem.lower()).strip("-")[:40] or "source"
    candidate = f"{base}-{sha256[:8]}"
    n = 2
    while candidate in taken:
        candidate = f"{base}-{sha256[:8]}-{n}"
        n += 1
    return candidate


def add_source(
    workspace: Workspace, rel_path: str, size_bytes: int, sha256: str
) -> tuple[Workspace, dict[str, Any], bool]:
    """Record an imported source file (#21) in ``workspace.json``.

    Returns the updated workspace, the entry and whether it is new. The same path with
    the same bytes returns the existing entry. A changed file gets a new entry, so a
    recipe that names the old ``source_id`` still describes the bytes it was made for.
    """
    meta = workspace.root / WORKSPACE_META_DIR
    with _sources_lock:
        doc = _read_document(meta)
        state, message = _classify(doc)
        if state != WORKSPACE:
            raise WorkspaceError(state, message)
        sources: list[dict[str, Any]] = doc["sources"]
        for entry in sources:
            if entry["path"] == rel_path and entry["sha256"] == sha256:
                return replace(workspace, document=doc), entry, False
        entry = {
            "id": _source_id(rel_path, sha256, {e["id"] for e in sources}),
            "path": rel_path,
            "sha256": sha256,
            "size_bytes": size_bytes,
        }
        sources.append(entry)
        try:
            atomic_write_json(meta / WORKSPACE_FILE, doc, WORKSPACE_SCHEMA)
        except PermissionError:
            raise WorkspaceError(
                "permission", "You don't have permission to update this workspace."
            ) from None
        except OSError:
            raise WorkspaceError("io", "The workspace file could not be updated.") from None
        return replace(workspace, document=doc), entry, True


# --- recently used workspaces -------------------------------------------------------------


class RecentWorkspaces:
    """Recently opened workspace roots, kept in the app state dir (paths only)."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()

    def load(self) -> list[dict[str, str]]:
        try:
            doc = json.loads(self.path.read_text("utf-8"))
            validate(doc, RECENT_SCHEMA)
        except FileNotFoundError:
            return []
        except (OSError, ValueError, ValidationError):
            log.warning("Ignoring an unreadable recent-workspaces file")
            return []
        return list(doc["workspaces"])

    def paths(self) -> list[str]:
        return [w["path"] for w in self.load()]

    def remember(self, root: Path) -> None:
        entry = {
            "path": str(root),
            "opened_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        with self._lock:
            rest = [w for w in self.load() if w["path"] != entry["path"]]
            doc = {"schema_version": "1.0", "workspaces": [entry, *rest][:RECENT_MAX]}
            atomic_write_json(self.path, doc, RECENT_SCHEMA)
