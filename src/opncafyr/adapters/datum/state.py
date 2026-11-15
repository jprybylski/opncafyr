"""The launcher's record for external data: ``.opncafyr/datafetch/state.json`` (#83, #85).

It holds the last check (a check never touches the study's lockfile, so it never shows up
as a Git change) and, per dataset, when the last Apply put its files in the study. The
files' sizes and SHA-256s are in datum's lockfile (``size``, ``dir_files``, datum 1.7.0),
and ``datum audit --verify-local`` says whether the study still matches them.

The schema is versioned (``schemas/datafetch-state-v2``) and the file is written
atomically. A v1 file (which also kept each applied file's hash and size) is read as v2:
``fetched_at`` becomes ``applied_at`` and the hashes are dropped; the next write saves v2.
"""

from __future__ import annotations

import hashlib
import json
import threading
from datetime import UTC, datetime
from functools import cache
from pathlib import Path
from typing import Any

from opncafyr.adapters.datum import HERE, STATE_FILE
from opncafyr.core.manifest import atomic_write_json, schema_problems

SCHEMA = "datafetch-state-v2"
SCHEMA_V1 = "datafetch-state-v1"
HASH_CHUNK = 1024 * 1024
_lock = threading.Lock()


def now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def fingerprint(path: Path) -> tuple[int, str]:
    """Size and SHA-256 of a file, read in chunks."""
    digest = hashlib.sha256()
    size = 0
    with Path(path).open("rb") as fh:
        while chunk := fh.read(HASH_CHUNK):
            digest.update(chunk)
            size += len(chunk)
    return size, digest.hexdigest()


@cache
def schema(name: str = SCHEMA) -> dict[str, Any]:
    return json.loads((HERE / "schemas" / f"{name}.schema.json").read_text("utf-8"))


def empty() -> dict[str, Any]:
    return {"schema_version": 2, "last_check": None, "datasets": {}}


def migrate_v1(doc: dict[str, Any]) -> dict[str, Any]:
    """A valid v1 state as v2: the Apply time per dataset, without the file hashes."""
    return {
        "schema_version": 2,
        "last_check": doc["last_check"],
        "datasets": {k: {"applied_at": v["fetched_at"]} for k, v in doc["datasets"].items()},
    }


def read(study: Path) -> dict[str, Any]:
    """The state, or an empty one when it is missing or unreadable."""
    try:
        doc = json.loads((study / STATE_FILE).read_text("utf-8"))
    except (OSError, ValueError):
        return empty()
    if isinstance(doc, dict) and doc.get("schema_version") == 1:
        if schema_problems(doc, schema(SCHEMA_V1), "state.json"):
            return empty()
        return migrate_v1(doc)
    if schema_problems(doc, schema(), "state.json"):
        return empty()
    return doc


def write(study: Path, doc: dict[str, Any]) -> None:
    problems = schema_problems(doc, schema(), "state.json")
    if problems:
        raise ValueError("; ".join(problems[:3]))
    atomic_write_json(study / STATE_FILE, doc)


def update(study: Path, change: Any) -> dict[str, Any]:
    """Read, apply ``change(doc)`` and write, under one lock."""
    with _lock:
        doc = read(study)
        change(doc)
        write(study, doc)
        return doc


def record_check(study: Path, results: list[dict[str, Any]]) -> None:
    def change(doc: dict[str, Any]) -> None:
        doc["last_check"] = {"at": now(), "results": results}

    update(study, change)


def record_apply(study: Path, datasets: list[str]) -> None:
    def change(doc: dict[str, Any]) -> None:
        at = now()
        for dataset in datasets:
            doc["datasets"][dataset] = {"applied_at": at}

    update(study, change)


def forget(study: Path, dataset: str) -> None:
    def change(doc: dict[str, Any]) -> None:
        doc["datasets"].pop(dataset, None)

    update(study, change)
