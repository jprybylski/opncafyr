"""The launcher's record for external data: ``.opncafyr/datafetch/state.json`` (#83).

It holds the last check (a check never touches the study's lockfile, so it never shows up
as a Git change) and, per dataset, the size and SHA-256 of every file the last Apply put
in the study, so the page can say whether the local copy still matches what was fetched.
The schema (``schemas/datafetch-state-v1``) is versioned and the file is written
atomically. Until datum records content hashes for every target itself (upstream U7),
this is where they live.
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

SCHEMA = "datafetch-state-v1"
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
def schema() -> dict[str, Any]:
    return json.loads((HERE / "schemas" / f"{SCHEMA}.schema.json").read_text("utf-8"))


def empty() -> dict[str, Any]:
    return {"schema_version": 1, "last_check": None, "datasets": {}}


def read(study: Path) -> dict[str, Any]:
    """The state, or an empty one when it is missing or unreadable."""
    try:
        doc = json.loads((study / STATE_FILE).read_text("utf-8"))
    except (OSError, ValueError):
        return empty()
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


def record_fetch(
    study: Path, dataset: str, remote: str | None, files: list[dict[str, Any]]
) -> None:
    def change(doc: dict[str, Any]) -> None:
        doc["datasets"][dataset] = {
            "fetched_at": now(),
            "remote_fingerprint": remote,
            "files": files,
        }

    update(study, change)


def forget(study: Path, dataset: str) -> None:
    def change(doc: dict[str, Any]) -> None:
        doc["datasets"].pop(dataset, None)

    update(study, change)
