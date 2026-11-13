"""Run bundles: one folder per analysis run, described by a validated manifest (#26).

``.opncafyr/runs/<run-id>/manifest.json`` (``run-manifest-v1``) is read and written only
through this module: every write validates the document and goes through
``atomic_write_json`` (temp file, fsync, rename), so a crash leaves the old manifest or the
new one, never a mix. Core knows run folders and states, not what a workflow puts in them.

- A run is created in ``.<run-id>.staging/`` and renamed into place (``stage_run``,
  ``publish_run``), so a half-made run never appears under its id.
- Its state mirrors the job states (``core.jobs``): ``preparing -> running -> reviewing ->
  completed``, or ``failed``. Staying in the same state is allowed; anything else must be
  a legal transition.
- ``seal_run`` completes a run: it hashes every file except the manifest into
  ``sealed.files``, writes the manifest, then makes every file read-only (and, on POSIX,
  every folder). From then on ``write_manifest`` refuses with ``RunSealedError``; a change
  means a new run.
- ``verify_run`` recomputes the hashes and reports files changed, missing or added.
  ``reuse=True`` (lists only, #67) skips the hashing when no file of a sealed run changed
  size or time since its last full check; publishing, building and **Check files** always
  hash every file.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import stat
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jsonschema import ValidationError

from opncafyr.core.jobs import JobState, JobStateError, transition
from opncafyr.core.manifest import atomic_write_json, file_signature, read_document, validate
from opncafyr.core.workspace import WorkspaceAccessError, resolve_in_root
from opncafyr.paths import WORKSPACE_META_DIR

RUNS_DIR = f"{WORKSPACE_META_DIR}/runs"
MANIFEST = "manifest.json"
SCHEMA = "run-manifest-v1"
RUN_ID_RE = re.compile(r"^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{4}$")
STAGING = ".{run_id}.staging"
_TEMP = re.compile(r"^\.manifest\.json\..*\.tmp$")


class RunError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class RunSealedError(RunError):
    def __init__(self, run_id: str) -> None:
        super().__init__(
            "run_sealed",
            f"Run {run_id} is completed and can't change. Make a new run instead.",
        )


def now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def runs_root(root: Path) -> Path:
    return resolve_in_root(root, RUNS_DIR)


def run_dir(root: Path, run_id: str) -> Path:
    """The folder of an existing run. Raises ``RunError("not_found")``."""
    if not RUN_ID_RE.match(run_id):
        raise RunError("not_found", "There is no such run.")
    try:
        folder = resolve_in_root(root, f"{RUNS_DIR}/{run_id}")
    except WorkspaceAccessError:
        raise RunError("not_found", "There is no such run.") from None
    if not folder.is_dir() or folder.is_symlink():
        raise RunError("not_found", "There is no such run.")
    return folder


def stage_run(root: Path) -> tuple[str, Path]:
    """A fresh run id and its empty staging folder."""
    runs = runs_root(root)
    runs.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    while True:
        run_id = f"{stamp}-{secrets.token_hex(2)}"
        staging = runs / STAGING.format(run_id=run_id)
        if not (runs / run_id).exists() and not staging.exists():
            staging.mkdir()
            return run_id, staging


def publish_run(staging: Path, run_id: str) -> Path:
    """Rename a complete staging folder to its run id, in one step."""
    target = staging.parent / run_id
    read_manifest(staging, run_id)  # never publish a folder without a valid manifest
    os.replace(staging, target)
    return target


def read_manifest(
    folder: Path, run_id: str | None = None, *, cached: bool = True
) -> dict[str, Any]:
    """The validated manifest of ``folder``. Raises ``RunError("invalid")``.

    ``cached`` reads the file again only if it changed (``core.manifest.read_document``);
    ``verify_run`` reads it afresh.
    """
    name = run_id or folder.name
    try:
        if cached:
            doc = read_document(folder / MANIFEST, SCHEMA)[0]
        else:
            doc = json.loads((folder / MANIFEST).read_text("utf-8"))
            validate(doc, SCHEMA)
    except (OSError, ValueError, ValidationError) as exc:
        detail = exc.message if isinstance(exc, ValidationError) else type(exc).__name__
        raise RunError("invalid", f"The manifest of run {name} is not valid: {detail}") from None
    if doc["run_id"] != name:
        raise RunError("invalid", f"The manifest in {name} names another run.")
    return doc


def write_manifest(folder: Path, doc: dict[str, Any]) -> dict[str, Any]:
    """Validate and atomically write ``doc``; refused once the run is sealed."""
    path = folder / MANIFEST
    if path.exists():
        current = read_manifest(folder, doc["run_id"])
        if current["state"] == JobState.COMPLETED:
            raise RunSealedError(doc["run_id"])
    atomic_write_json(path, doc, SCHEMA)
    return doc


def set_state(folder: Path, state: str, **updates: Any) -> dict[str, Any]:
    """Move the run to ``state`` (or stay in it) and apply ``updates`` to the manifest."""
    doc = read_manifest(folder)
    if doc["state"] == JobState.COMPLETED:
        raise RunSealedError(doc["run_id"])
    if doc["state"] != state:
        try:
            transition(JobState(doc["state"]), JobState(state))
        except JobStateError as exc:
            raise RunError("bad_state", str(exc)) from None
    doc.update(updates)
    doc["state"] = state
    return write_manifest(folder, doc)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def manifest_sha256(folder: Path) -> str:
    """The SHA-256 of the manifest's bytes (read again only if it changed)."""
    try:
        return read_document(folder / MANIFEST, SCHEMA)[1]
    except (OSError, ValueError, ValidationError):
        return sha256_file(folder / MANIFEST)


def _walk(folder: Path) -> list[tuple[str, os.DirEntry]]:
    """Every file under ``folder`` as (relative POSIX path, entry), the manifest included.

    ``os.scandir`` answers "link? folder? size? time?" from the directory listing itself
    (on Windows without opening each file), where ``rglob`` plus ``is_symlink`` and
    ``is_file`` cost several calls per file (#67).
    """
    out: list[tuple[str, os.DirEntry]] = []
    pending = [(os.fspath(folder), "")]
    while pending:
        directory, prefix = pending.pop()
        with os.scandir(directory) as entries:
            for entry in entries:
                rel = prefix + entry.name
                if entry.is_symlink() or entry.is_junction():
                    raise RunError("unsafe", f"{rel} is a link; runs hold files only.")
                if entry.is_dir(follow_symlinks=False):
                    pending.append((entry.path, rel + "/"))
                elif entry.is_file(follow_symlinks=False):
                    out.append((rel, entry))
    return out


def _files(folder: Path) -> list[Path]:
    return sorted(folder / rel for rel, _ in _walk(folder) if _sealed_rel(rel))


def hash_files(folder: Path) -> list[dict[str, Any]]:
    return [
        {
            "path": p.relative_to(folder).as_posix(),
            "sha256": sha256_file(p),
            "bytes": p.stat().st_size,
        }
        for p in _files(folder)
    ]


def _read_only(path: Path) -> None:
    mode = path.stat().st_mode
    path.chmod(mode & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))


def seal_run(folder: Path, **updates: Any) -> dict[str, Any]:
    """Complete a reviewing run: hash every file, write the manifest, make it all read-only."""
    doc = read_manifest(folder)
    if doc["state"] == JobState.COMPLETED:
        raise RunSealedError(doc["run_id"])
    doc.update(updates)
    doc["sealed"] = {"at": now(), "files": hash_files(folder)}
    set_state(folder, JobState.COMPLETED, **{k: v for k, v in doc.items() if k != "state"})
    for path in [*_files(folder), folder / MANIFEST]:
        _read_only(path)
    if os.name != "nt":  # Windows ignores read-only on folders
        for path in sorted((p for p in folder.rglob("*") if p.is_dir()), reverse=True):
            _read_only(path)
        _read_only(folder)
    return read_manifest(folder)


# Full checks of sealed runs, by folder: (what every file looked like, the report).
_verified: dict[str, tuple[tuple, dict[str, Any]]] = {}
_verified_lock = threading.Lock()


def _tree_signature(walked: list[tuple[str, os.DirEntry]]) -> tuple:
    """Size and time of every file walked (the manifest included)."""
    return tuple(sorted((rel, *file_signature(e.stat(follow_symlinks=False))) for rel, e in walked))


def _sealed_rel(rel: str) -> bool:
    return rel != MANIFEST and not _TEMP.match(rel.rpartition("/")[2])


def verify_run(folder: Path, *, reuse: bool = False) -> dict[str, Any]:
    """Recompute hashes and compare with the manifest.

    A sealed run is checked file by file (changed, missing, added). A run not yet sealed
    is checked for what its manifest names: the input, settings, script, record and
    artifacts.

    ``reuse`` is for lists that show whether runs can be published (#67): if no file of a
    sealed run changed size or modification time since a full check in this process, that
    check's report is returned without hashing. Anything that uses a run's files
    (publishing, report and deck builds) and **Check files** call it without ``reuse``.
    """
    key = os.fspath(folder)
    try:
        walked: list[tuple[str, os.DirEntry]] | None = _walk(folder)
        before = _tree_signature(walked)
    except (OSError, RunError):
        walked = before = None
    if reuse and before is not None:
        with _verified_lock:
            hit = _verified.get(key)
        if hit is not None and hit[0] == before:
            return {**hit[1], **{k: list(hit[1][k]) for k in ("changed", "missing", "added")}}
    report = _verify(folder, walked)
    if report["sealed"] and before is not None:
        with _verified_lock:
            _verified[key] = (before, report)
    return report


def _verify(folder: Path, walked: list[tuple[str, os.DirEntry]] | None) -> dict[str, Any]:
    report: dict[str, Any] = {
        "run_id": folder.name,
        "ok": False,
        "changed": [],
        "missing": [],
        "added": [],
        "sealed": False,
    }
    try:
        doc = read_manifest(folder, cached=False)
    except RunError as exc:
        report["error"] = exc.message
        return report
    report["state"] = doc["state"]
    if "sealed" in doc:
        report["sealed"] = True
        expected = {f["path"]: f["sha256"] for f in doc["sealed"]["files"]}
    else:
        expected = {doc["analysis_input"]["path"]: doc["analysis_input"]["sha256"]}
        for key in ("settings", "script", "record"):
            if key in doc:
                expected[doc[key]["path"]] = doc[key]["sha256"]
        for art in doc.get("artifacts", []):
            expected[art["path"]] = art["sha256"]
    for rel, sha in sorted(expected.items()):
        try:
            path = resolve_in_root(folder, rel)
        except WorkspaceAccessError:
            report["changed"].append(rel)
            continue
        if not path.is_file():
            report["missing"].append(rel)
        elif sha256_file(path) != sha:
            report["changed"].append(rel)
    if report["sealed"]:
        if walked is None:  # raises RunError for a link, as before
            present = {p.relative_to(folder).as_posix() for p in _files(folder)}
        else:
            present = {rel for rel, _ in walked if _sealed_rel(rel)}
        report["added"] = sorted(present - set(expected))
    report["ok"] = not (report["changed"] or report["missing"] or report["added"])
    return report
