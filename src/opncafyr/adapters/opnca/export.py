"""opnca-r's server-side save into a run folder (``POST api/v1/export/save``, #28).

opnca-r (0.2.9 and later) writes ``analysis.R``, ``analysis-record.json``, ``results/**`` and
``artifacts.json`` through its own staging folder (``.staging-*``) under a lock
(``.opnca-save.lock/``), then ``COMPLETE`` last. This module only reads that output and
decides whether it is a complete, intact save. It never reads a browser download.

A save is **complete** only if:

- ``COMPLETE`` exists and its ``artifacts_sha256`` is the SHA-256 of ``artifacts.json``;
- every artifact listed re-hashes to its ``sha256`` and ``bytes``, sits inside the run
  folder under ``analysis.R``, ``analysis-record.json`` or ``results/``, and nothing in
  ``results/`` is unlisted;
- ``analysis.R`` and ``analysis-record.json`` are listed, the record's ``data.sha256`` is
  the snapshot's SHA-256 and its ``data.path`` and the script name the snapshot by its
  relative path;
- nothing else sits at the top of the folder except the caller's own entries.

While the lock or a staging folder exists and the session is alive, the save is
**saving**. Anything else with opnca-r output in it is **failed**: upstream reports such
a folder as already saved for ever, so it can never become complete.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

COMPLETE = "COMPLETE"
ARTIFACTS = "artifacts.json"
SCRIPT = "analysis.R"
RECORD = "analysis-record.json"
RESULTS = "results"
LOCK = ".opnca-save.lock"
STAGING_PREFIX = ".staging-"
UPSTREAM = (SCRIPT, RECORD, ARTIFACTS, RESULTS, COMPLETE)
INPUT = "input/analysis.csv"

KINDS = {
    SCRIPT: "script",
    RECORD: "record",
}


@dataclass
class ExportStatus:
    state: str  # none | saving | complete | failed
    problems: list[str] = field(default_factory=list)
    complete: dict[str, Any] | None = None
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    record: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"state": self.state, "problems": self.problems}


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _inside(folder: Path, rel: str) -> Path | None:
    if not rel or rel.startswith(("/", "\\")) or "\\" in rel or ".." in rel.split("/"):
        return None
    path = (folder / rel).resolve()
    try:
        path.relative_to(folder.resolve())
    except ValueError:
        return None
    return path


def kind_of(rel: str, media_type: str) -> str:
    if rel in KINDS:
        return KINDS[rel]
    if media_type.startswith("image/"):
        return "figure"
    if media_type == "text/csv":
        return "table"
    return "other"


def inspect_export(
    folder: Path, *, own: Iterable[str], snapshot_sha256: str, session_alive: bool
) -> ExportStatus:
    """What opnca-r's save in ``folder`` amounts to. ``own`` are the caller's entries."""
    own = set(own)
    names = {p.name for p in folder.iterdir()}
    in_progress = LOCK in names or any(n.startswith(STAGING_PREFIX) for n in names)
    upstream = names & set(UPSTREAM)
    if not upstream and not in_progress:
        return ExportStatus("none")
    if in_progress:
        if session_alive:
            return ExportStatus("saving")
        return ExportStatus(
            "failed", ["opnca-r's save did not finish (its lock or staging folder is still there)."]
        )
    stray = sorted(n for n in names - own - set(UPSTREAM) if not _temp(n))
    if COMPLETE not in names:
        return ExportStatus("failed", ["opnca-r wrote part of a save but not COMPLETE."])
    problems: list[str] = []
    if stray:
        problems.append(f"Unexpected files in the run folder: {', '.join(stray)}.")
    try:
        complete = json.loads((folder / COMPLETE).read_text("utf-8"))
        listing = json.loads((folder / ARTIFACTS).read_text("utf-8"))
        artifacts = list(listing["artifacts"])
    except (OSError, ValueError, KeyError, TypeError):
        return ExportStatus("failed", ["COMPLETE or artifacts.json can't be read."])
    if complete.get("artifacts_sha256") != _sha(folder / ARTIFACTS):
        problems.append("artifacts.json does not match the hash in COMPLETE.")
    listed: set[str] = set()
    for art in artifacts:
        rel = str(art.get("path", ""))
        path = _inside(folder, rel)
        if path is None or not (rel in (SCRIPT, RECORD) or rel.startswith(RESULTS + "/")):
            problems.append(f"artifacts.json lists a path outside the save: {rel!r}.")
            continue
        listed.add(rel)
        if not path.is_file() or path.is_symlink():
            problems.append(f"{rel} is missing.")
        elif path.stat().st_size != art.get("bytes") or _sha(path) != art.get("sha256"):
            problems.append(f"{rel} does not match artifacts.json.")
    for required in (SCRIPT, RECORD):
        if required not in listed:
            problems.append(f"artifacts.json does not list {required}.")
    results = folder / RESULTS
    if results.is_dir():
        for path in sorted(results.rglob("*")):
            rel = path.relative_to(folder).as_posix()
            if path.is_symlink() or (path.is_file() and rel not in listed):
                problems.append(f"{rel} is not listed in artifacts.json.")
    record = None
    try:
        record = json.loads((folder / RECORD).read_text("utf-8"))
        data = record.get("data") or {}
        if data.get("sha256") != snapshot_sha256:
            problems.append("The analysis record names a different input than the snapshot.")
        if data.get("path") != INPUT:
            problems.append(f"The analysis record does not read {INPUT}.")
    except (OSError, ValueError):
        problems.append("analysis-record.json can't be read.")
    try:
        if f'"{INPUT}"' not in (folder / SCRIPT).read_text("utf-8"):
            problems.append(f"analysis.R does not read {INPUT} by its relative path.")
    except OSError:
        pass  # reported above as missing
    if problems:
        return ExportStatus("failed", problems)
    for art in artifacts:
        art["kind"] = kind_of(art["path"], art.get("media_type", ""))
    return ExportStatus("complete", [], complete, artifacts, record)


def _temp(name: str) -> bool:
    return name.startswith(".") and name.endswith(".tmp")
