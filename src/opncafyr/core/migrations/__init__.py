"""Schema migrations for the workspace metadata (``.opncafyr/workspace.json``).

Each workspace records its ``schema_version``. Opening one with an older version runs the
ordered steps in ``MIGRATIONS`` from that version up to ``CURRENT_VERSION``:

1. ``workspace.json`` (and any other file a step touches, through ``MigrationContext.
   backup``) is copied to ``.opncafyr/backups/`` first;
2. each step turns the document into the next version's shape;
3. the result must validate against the current schema;
4. only then is it written over the original, atomically.

A failed step or validation leaves the original untouched. A workspace with a version
newer than ``CURRENT_VERSION`` is never opened or rewritten.

To add a version: bump ``CURRENT_VERSION``, add ``schemas/workspace-vN.schema.json``, and
append a ``Migration`` from the previous version. Never edit a released step.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from opncafyr.core.manifest import atomic_write_json, validate
from opncafyr.core.migrations import v0_to_v1

CURRENT_VERSION = "1.0"
WORKSPACE_SCHEMA = "workspace-v1"
WORKSPACE_FILE = "workspace.json"
BACKUP_DIR = "backups"

_VERSION_RE = re.compile(r"^(0|[1-9][0-9]*)(?:\.(0|[1-9][0-9]*))?$")


class MigrationError(RuntimeError):
    """A migration could not run. The original files are unchanged."""


@dataclass
class MigrationContext:
    meta_dir: Path
    backup_dir: Path
    stamp: str
    backups: list[Path] = field(default_factory=list)

    def backup(self, path: Path, label: str) -> Path:
        """Copy ``path`` into the backup folder before a step changes it."""
        target = self.backup_dir / f"{path.stem}-{label}-{self.stamp}{path.suffix}"
        n = 1
        while target.exists():
            n += 1
            target = self.backup_dir / f"{path.stem}-{label}-{self.stamp}-{n}{path.suffix}"
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        with path.open("rb") as src, target.open("xb") as dst:
            dst.write(src.read())
        self.backups.append(target)
        return target


@dataclass(frozen=True)
class Migration:
    from_version: str
    to_version: str
    apply: Callable[[dict[str, Any], MigrationContext], dict[str, Any]]


MIGRATIONS: tuple[Migration, ...] = (Migration("0", "1.0", v0_to_v1.apply),)


def parse_version(text: object) -> tuple[int, int]:
    """``"1"`` -> (1, 0), ``"1.2"`` -> (1, 2). Raises ValueError for anything else."""
    if not isinstance(text, str):
        raise ValueError("schema_version is not a string")
    match = _VERSION_RE.match(text)
    if match is None:
        raise ValueError("schema_version is not N or N.M")
    return int(match.group(1)), int(match.group(2) or 0)


def chain(from_version: str) -> list[Migration]:
    """The ordered steps from ``from_version`` to the current version."""
    steps: list[Migration] = []
    version = from_version
    while version != CURRENT_VERSION:
        step = next((m for m in MIGRATIONS if m.from_version == version), None)
        if step is None:
            raise MigrationError(f"There is no upgrade path from schema {from_version}.")
        steps.append(step)
        version = step.to_version
    return steps


@dataclass(frozen=True)
class MigrationResult:
    document: dict[str, Any]
    from_version: str
    backups: list[Path]


def migrate(meta_dir: Path, document: dict[str, Any]) -> MigrationResult:
    """Upgrade ``meta_dir/workspace.json`` (already loaded as ``document``)."""
    from_version = document.get("schema_version")
    if not isinstance(from_version, str):
        raise MigrationError("The workspace file has no schema version.")
    steps = chain(from_version)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    ctx = MigrationContext(meta_dir=meta_dir, backup_dir=meta_dir / BACKUP_DIR, stamp=stamp)
    source = meta_dir / WORKSPACE_FILE
    ctx.backup(source, f"v{from_version}")
    doc = dict(document)
    try:
        for step in steps:
            doc = step.apply(doc, ctx)
            if doc.get("schema_version") != step.to_version:
                raise MigrationError(f"The step to schema {step.to_version} did not finish.")
        validate(doc, WORKSPACE_SCHEMA)
    except MigrationError:
        raise
    except Exception as exc:
        raise MigrationError(
            f"Upgrading the workspace from schema {from_version} failed ({type(exc).__name__})."
        ) from exc
    atomic_write_json(source, doc, WORKSPACE_SCHEMA)
    return MigrationResult(document=doc, from_version=from_version, backups=list(ctx.backups))
