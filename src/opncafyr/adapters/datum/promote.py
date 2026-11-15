"""Apply a reviewed fetch to the study, and undo or finish one that was interrupted (#83).

Apply is the only thing that changes study files. The caller holds the workspace lease
and has checked that no job is running. Steps:

The review folder is datum's staged fetch (``--stage-dir``: ``files/<target>``;
``--lock-out``: ``lock.yaml``).

1. **Verify.** The review folder's files still have the hashes the review lists; every
   study file still has the hash the review showed as "current"; ``.data.yaml`` and the
   lockfile have not changed since the fetch.
2. **Journal.** ``.opncafyr/datafetch/promote.json`` lists every move, and a copy of the
   old lockfile is kept next to it. While the journal exists the page shows "Apply was
   interrupted" and offers Finish or Roll back.
3. **Keep replaced bytes** (D2) under ``previous/<dataset>/<sha256>``.
4. **Move** each new file into place with an atomic replace; remove files a directory
   source dropped (only paths the dataset itself wrote).
5. **Lockfile last**, with an atomic replace (datum's ``--lock-out``, which records each
   file's SHA-256 and size), then the Apply time in ``state.json``; then the journal and
   the review folder go.

Completed runs are never affected: each run keeps its own copy of its input, and the
next analysis is a new run.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
from pathlib import Path
from typing import Any

from opncafyr.adapters.datum import (
    CONFIG_FILE,
    FETCH_CAPABILITY,
    JOURNAL_FILE,
    LOCK_FILE,
    META,
    PREVIOUS_DIR,
    REVIEW_DOCUMENT,
    contract,
    state,
)
from opncafyr.adapters.datum import config as cfg
from opncafyr.adapters.datum.staging import (
    FILES,
    SNAPSHOT_LOCK,
    SnapshotError,
    lock_sha256,
    review_dir,
    study_file,
)
from opncafyr.core.manifest import atomic_write_bytes, atomic_write_json

OLD_LOCK = f"{META}/promote-lock.old"
NO_LOCK = "none"


class PromoteError(RuntimeError):
    def __init__(self, code: str, message: str, status: int = 409) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


# --- hooks for tests that stop a promote part-way ----------------------------------------


def _step(name: str) -> None:
    """Called between promote steps; tests patch it to simulate a crash."""


# --- reviews -----------------------------------------------------------------------------


def load_review(study: Path, review_id: str) -> dict[str, Any]:
    try:
        folder = review_dir(study, review_id)
    except SnapshotError as exc:
        raise PromoteError("not_found", exc.message, 404) from None
    try:
        doc = json.loads((folder / REVIEW_DOCUMENT).read_text("utf-8"))
    except (OSError, ValueError):
        raise PromoteError("not_found", "This fetch review no longer exists.", 404) from None
    problems = contract(FETCH_CAPABILITY).check_output(doc, REVIEW_DOCUMENT)
    if problems or doc.get("review_id") != review_id:
        raise PromoteError("bad_review", "This fetch review is damaged. Discard it.")
    return doc


def list_reviews(study: Path) -> list[dict[str, Any]]:
    folder = study / META / "reviews"
    out = []
    if not folder.is_dir():
        return out
    for child in sorted(folder.iterdir()):
        if child.suffix or not child.is_dir():
            continue  # *.staging folders belong to a running job
        try:
            doc = load_review(study, child.name)
        except PromoteError:
            continue
        out.append({"review_id": doc["review_id"], "at": doc["at"]})
    return out


def discard(study: Path, review_id: str) -> None:
    try:
        folder = review_dir(study, review_id)
    except SnapshotError as exc:
        raise PromoteError("not_found", exc.message, 404) from None
    if folder.is_dir():
        shutil.rmtree(folder)


def _sha(path: Path) -> str | None:
    if not path.is_file() or path.is_symlink():
        return None
    return state.fingerprint(path)[1]


def _moves(doc: dict[str, Any]) -> list[dict[str, Any]]:
    moves = []
    for ds in doc["datasets"]:
        for f in ds["files"]:
            if f["action"] == "unchanged":
                continue
            moves.append(
                {
                    "dataset": ds["id"],
                    "path": f["path"],
                    "action": f["action"],
                    "old_sha256": f.get("current_sha256"),
                    "new_sha256": f.get("new_sha256"),
                }
            )
    return moves


# --- apply -------------------------------------------------------------------------------


def journal(study: Path) -> dict[str, Any] | None:
    try:
        doc = json.loads((study / JOURNAL_FILE).read_text("utf-8"))
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def apply(study: Path, review_id: str) -> dict[str, Any]:
    """Move a reviewed fetch into the study. Returns a short summary."""
    study = study.resolve()
    if journal(study) is not None:
        raise PromoteError(
            "interrupted", "An earlier Apply was interrupted. Finish or roll it back first."
        )
    doc = load_review(study, review_id)
    folder = review_dir(study, review_id)
    try:
        current_config = cfg.load(study).sha256
    except cfg.ConfigError:
        current_config = None
    if current_config != doc["config_sha256"]:
        raise PromoteError(
            "config_changed",
            f"{CONFIG_FILE} changed after this fetch. Discard the review and fetch again.",
        )
    if lock_sha256(study) != doc["lock_sha256"]:
        raise PromoteError(
            "lock_changed",
            f"{LOCK_FILE} changed after this fetch. Discard the review and fetch again.",
        )
    moves = _moves(doc)
    try:
        for m in moves:
            dest = study_file(study, m["path"])
            if _sha(dest) != m["old_sha256"]:
                raise PromoteError(
                    "file_changed",
                    f"{m['path']} changed after this fetch. Discard the review and fetch again.",
                )
            if m["action"] != "remove" and _sha(folder / FILES / m["path"]) != m["new_sha256"]:
                raise PromoteError("bad_review", "This fetch review is damaged. Discard it.")
    except SnapshotError as exc:
        raise PromoteError("bad_review", exc.message) from None
    staged_lock = folder / SNAPSHOT_LOCK
    if not staged_lock.is_file():
        raise PromoteError("bad_review", "This fetch review has no lockfile. Discard it.")

    # The old lock is kept until the journal goes, so a roll back can restore it.
    old_lock = study / LOCK_FILE
    if old_lock.is_file():
        atomic_write_bytes(study / OLD_LOCK, old_lock.read_bytes())
    atomic_write_json(
        study / JOURNAL_FILE,
        {
            "schema_version": 1,
            "review_id": review_id,
            "started_at": state.now(),
            "lock_sha256": doc["lock_sha256"] or NO_LOCK,
            "new_lock_sha256": _sha(staged_lock),
            "moves": moves,
        },
    )
    _step("journal")
    _run_moves(study, folder, moves)
    _finish_lock_and_state(study, folder, doc)
    changed = sum(1 for m in moves)
    return {"review_id": review_id, "changed_files": changed}


def _keep_previous(study: Path, dataset: str, path: Path, sha: str) -> None:
    dest = study / PREVIOUS_DIR / dataset / sha
    if dest.is_file() and _sha(dest) == sha:
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(f".{sha}.tmp")
    shutil.copyfile(path, tmp)
    os.replace(tmp, dest)


def _run_moves(study: Path, folder: Path, moves: list[dict[str, Any]]) -> None:
    for m in moves:
        dest = study_file(study, m["path"])
        if m["old_sha256"] and _sha(dest) == m["old_sha256"]:
            _keep_previous(study, m["dataset"], dest, m["old_sha256"])
    _step("previous")
    for m in moves:
        dest = study_file(study, m["path"])
        current = _sha(dest)
        if m["action"] == "remove":
            if current == m["old_sha256"]:
                dest.unlink()
        elif current != m["new_sha256"]:
            staged = folder / FILES / m["path"]
            if _sha(staged) != m["new_sha256"]:
                raise PromoteError(
                    "cannot_finish",
                    "The fetched files are gone, so Apply can't finish. Roll it back.",
                )
            dest.parent.mkdir(parents=True, exist_ok=True)
            os.replace(staged, dest)
        _step(f"move:{m['path']}")


def _finish_lock_and_state(study: Path, folder: Path, doc: dict[str, Any]) -> None:
    staged_lock = folder / SNAPSHOT_LOCK
    if staged_lock.is_file():
        atomic_write_bytes(study / LOCK_FILE, staged_lock.read_bytes())
    _step("lock")
    state.record_apply(study, [ds["id"] for ds in doc["datasets"] if ds["files"]])
    _step("state")
    (study / JOURNAL_FILE).unlink(missing_ok=True)
    (study / OLD_LOCK).unlink(missing_ok=True)
    shutil.rmtree(folder, ignore_errors=True)


# --- recovery ----------------------------------------------------------------------------


def finish(study: Path) -> dict[str, Any]:
    """Complete an interrupted Apply from its journal."""
    study = study.resolve()
    j = journal(study)
    if j is None:
        raise PromoteError("nothing", "There is no interrupted Apply.", 404)
    review_id = str(j.get("review_id"))
    doc = load_review(study, review_id)
    folder = review_dir(study, review_id)
    _run_moves(study, folder, j["moves"])
    _finish_lock_and_state(study, folder, doc)
    return {"review_id": review_id, "finished": True}


def rollback(study: Path) -> dict[str, Any]:
    """Put back every file and the lockfile as they were before an interrupted Apply."""
    study = study.resolve()
    j = journal(study)
    if j is None:
        raise PromoteError("nothing", "There is no interrupted Apply.", 404)
    for m in j["moves"]:
        dest = study_file(study, m["path"])
        current = _sha(dest)
        old = m.get("old_sha256")
        if old:
            if current != old:
                kept = study / PREVIOUS_DIR / m["dataset"] / old
                if _sha(kept) != old:
                    raise PromoteError(
                        "cannot_roll_back",
                        f"The earlier copy of {m['path']} is missing, so it can't be put back.",
                    )
                dest.parent.mkdir(parents=True, exist_ok=True)
                tmp = dest.with_name(f".{dest.name}.restore")
                shutil.copyfile(kept, tmp)
                os.replace(tmp, dest)
        elif current is not None and current == m.get("new_sha256"):
            dest.unlink()  # a new file this Apply added
    old_lock = study / OLD_LOCK
    if j.get("lock_sha256") == NO_LOCK:
        (study / LOCK_FILE).unlink(missing_ok=True)
    elif old_lock.is_file():
        atomic_write_bytes(study / LOCK_FILE, old_lock.read_bytes())
    (study / JOURNAL_FILE).unlink(missing_ok=True)
    old_lock.unlink(missing_ok=True)
    review = j.get("review_id")
    if isinstance(review, str):
        with contextlib.suppress(SnapshotError):
            shutil.rmtree(review_dir(study, review), ignore_errors=True)
    return {"rolled_back": True}


# --- delete, previous copies -------------------------------------------------------------


def keep_and_remove(study: Path, dataset: str, rels: list[str]) -> list[str]:
    """Move a dataset's files to ``previous/`` (for Delete). Returns the paths removed."""
    study = study.resolve()
    removed = []
    for rel in rels:
        path = study_file(study, rel)
        sha = _sha(path)
        if sha is None:
            continue
        _keep_previous(study, dataset, path, sha)
    for rel in rels:
        path = study_file(study, rel)
        if _sha(path) is not None:
            path.unlink()
            removed.append(rel)
    return removed


def previous_size(study: Path) -> dict[str, dict[str, int]]:
    """Per dataset: how many earlier copies are kept and their total size."""
    root = study / PREVIOUS_DIR
    out: dict[str, dict[str, int]] = {}
    if not root.is_dir():
        return out
    for folder in sorted(root.iterdir()):
        if not folder.is_dir():
            continue
        files = [f for f in folder.iterdir() if f.is_file() and not f.name.startswith(".")]
        out[folder.name] = {"files": len(files), "bytes": sum(f.stat().st_size for f in files)}
    return out


def purge(study: Path, dataset: str) -> None:
    if not cfg.ID_RE.match(dataset):
        raise PromoteError("not_found", "No such dataset.", 404)
    folder = study / PREVIOUS_DIR / dataset
    if folder.is_dir():
        shutil.rmtree(folder)
