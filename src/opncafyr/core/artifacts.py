"""Read-only listing of the runs and publications in a workspace (home page, #49).

Runs live in ``.opncafyr/runs/<run-id>/manifest.json`` (``run-manifest-v1``) and
publications in ``.opncafyr/publications/<id>.json`` (``publication-v1``); see
``docs/architecture.md``. This module only reads them. A run folder without a valid
manifest, or whose manifest names a different run, is listed as ``incomplete`` so that a
half-written output never looks complete. Nothing here writes, and no file path is
returned to the page. Symlinked entries are skipped.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from jsonschema import ValidationError

from opncafyr.core.manifest import validate
from opncafyr.core.workspace import WorkspaceAccessError, resolve_in_root
from opncafyr.paths import WORKSPACE_META_DIR

log = logging.getLogger(__name__)

RUNS_DIR = f"{WORKSPACE_META_DIR}/runs"
PUBLICATIONS_DIR = f"{WORKSPACE_META_DIR}/publications"
RUN_MANIFEST = "manifest.json"
RUN_SCHEMA = "run-manifest-v1"
PUBLICATION_SCHEMA = "publication-v1"
INCOMPLETE = "incomplete"

# A manifest is small; anything larger than this is not one we wrote.
MAX_DOCUMENT_BYTES = 4 * 1024 * 1024
# The home page shows a list, not an archive browser.
MAX_LISTED = 500


def _entries(root: Path, relative: str) -> list[Path]:
    """Children of a metadata folder, or nothing if it is missing or leaves the root."""
    try:
        folder = resolve_in_root(root, relative)
    except (WorkspaceAccessError, OSError):
        return []
    if not folder.is_dir():
        return []
    try:
        return sorted(folder.iterdir(), key=lambda p: p.name, reverse=True)[:MAX_LISTED]
    except OSError:
        return []


def _read(root: Path, relative: str, schema: str) -> dict[str, Any] | None:
    """The validated document at ``relative``, or None if it is missing or invalid."""
    try:
        path = resolve_in_root(root, relative)
        if not path.is_file() or path.stat().st_size > MAX_DOCUMENT_BYTES:
            return None
        document = json.loads(path.read_text("utf-8"))
        validate(document, schema)
    except (WorkspaceAccessError, OSError, ValueError, ValidationError):
        return None
    return document


def list_runs(root: Path) -> list[dict[str, Any]]:
    runs = []
    for entry in _entries(root, RUNS_DIR):
        if entry.name.startswith(".") or entry.is_symlink() or not entry.is_dir():
            continue
        manifest = _read(root, f"{RUNS_DIR}/{entry.name}/{RUN_MANIFEST}", RUN_SCHEMA)
        if manifest is None or manifest["run_id"] != entry.name:
            runs.append({"run_id": entry.name, "state": INCOMPLETE})
            continue
        analyte = manifest["analyte"]
        runs.append(
            {
                "run_id": manifest["run_id"],
                "created_at": manifest["created_at"],
                "state": manifest["state"],
                "analyte": {
                    "column": analyte["column"],
                    "value": analyte["value"],
                    "label": analyte.get("label"),
                },
            }
        )
    return runs


def list_publications(root: Path) -> list[dict[str, Any]]:
    publications = []
    for entry in _entries(root, PUBLICATIONS_DIR):
        if entry.name.startswith(".") or entry.is_symlink() or entry.suffix != ".json":
            continue
        doc = _read(root, f"{PUBLICATIONS_DIR}/{entry.name}", PUBLICATION_SCHEMA)
        if doc is None:
            publications.append({"publication_id": entry.stem, "state": INCOMPLETE})
            continue
        publications.append(
            {
                "publication_id": doc["publication_id"],
                "created_at": doc["created_at"],
                "state": "recorded",
                "items": len(doc["items"]),
            }
        )
    return publications
