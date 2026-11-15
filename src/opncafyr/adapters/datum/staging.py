"""Run datum on a snapshot, never on the study (#83, ADR 0012).

datum writes straight to its targets and lockfile, reconciles Git/SVN ignore rules for
any working copy around its cwd, and under the ``update`` policy re-fetches during a
check. So every datum process gets:

1. **A snapshot.** The checked config is written to a launcher-owned folder with
   ``ignore: false`` everywhere, relative ``file`` sources made absolute (under the study)
   and every target made absolute under the snapshot's ``files/`` folder. The study's
   lockfile is copied next to it, and the current target files are copied into
   ``files/`` so datum sees the same local state it would see in the study.
2. **A cwd outside every working copy.** Jobs use the job manager's ``isolated_cwd``; the
   adapter refuses to run if any folder above it holds ``.git`` or ``.svn``.
3. **A review.** datum's ``--json`` output is redacted, checked and turned into a review
   document. A check stops there (it records the result in ``state.json`` only); a fetch
   leaves a review folder with the new files that ``promote.apply`` moves into place
   after the user confirms.

The lockfile stores no target paths (ids to hashes, fingerprints and ``dir_paths``), so
the absolute snapshot targets never reach the study.
"""

from __future__ import annotations

import json
import re
import secrets
import shutil
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from opncafyr.adapters.datum import (
    CHECK_CAPABILITY,
    FETCH_CAPABILITY,
    LOCK_FILE,
    REVIEW_DOCUMENT,
    REVIEWS_DIR,
    SOURCE_DATA,
    DatumError,
    argv,
    contract,
    datum_env,
    executable,
    parse_json,
    quick,
    state,
)
from opncafyr.adapters.datum import config as cfg
from opncafyr.adapters.datum.redact import Redactor
from opncafyr.core.jobmanager import (
    CommandSpec,
    JobContext,
    JobPrepareError,
    JobRequest,
    ReviewResult,
)
from opncafyr.core.workspace import WorkspaceAccessError, resolve_in_root

REVIEW_ID_RE = re.compile(r"^[0-9a-f]{16}$")
STATUSES = ("ok", "updated", "fetched", "stale", "fail", "warn", "error", "deleted")
SNAPSHOT_CONFIG = "config.yaml"
SNAPSHOT_LOCK = "lock.yaml"
FILES = "files"
VCS_MARKERS = (".git", ".svn")


class SnapshotError(RuntimeError):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


# --- lockfile ------------------------------------------------------------------------------


def read_lock(path: Path) -> dict[str, dict[str, Any]]:
    """The lock's items by dataset id ({} when there is no lock yet)."""
    try:
        data = yaml.safe_load(path.read_text("utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        raise SnapshotError(f"{LOCK_FILE} could not be read.") from None
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, dict):
        return {}
    return {str(k): v for k, v in items.items() if isinstance(v, dict)}


def lock_sha256(study: Path) -> str | None:
    path = study / LOCK_FILE
    if not path.is_file():
        return None
    return state.fingerprint(path)[1]


def dir_paths(item: Mapping[str, Any] | None) -> list[str] | None:
    """A directory dataset's tracked paths, checked to stay under its target."""
    if not item or "dir_paths" not in item:
        return None
    paths = item.get("dir_paths") or []
    out = []
    for p in paths:
        pure = PurePosixPath(str(p))
        if pure.is_absolute() or ".." in pure.parts or "\\" in str(p) or not pure.parts:
            raise SnapshotError("The lockfile lists a path outside its dataset's folder.")
        out.append(pure.as_posix())
    return out


def tracked_files(study: Path, target: str, item: Mapping[str, Any] | None) -> list[str]:
    """Study-relative paths a dataset wrote: its target, or its directory's tracked files."""
    paths = dir_paths(item)
    if paths is None:
        return [target]
    return [f"{target}/{p}" for p in paths]


def study_file(study: Path, rel: str) -> Path:
    """``rel`` (under source-data/) resolved inside the study; refuses anything else."""
    if not rel.startswith(f"{SOURCE_DATA}/"):
        raise SnapshotError("A dataset path is outside source-data/.")
    try:
        path = resolve_in_root(study, rel)
        source = resolve_in_root(study, SOURCE_DATA)
    except WorkspaceAccessError:
        raise SnapshotError("A dataset path leads outside the study folder.") from None
    if not path.is_relative_to(source):
        raise SnapshotError("A dataset path leads outside source-data/.")
    return path


def check_cwd(cwd: Path) -> None:
    """Refuse a cwd inside a Git or SVN working copy: datum would edit its ignore rules."""
    folder = Path(cwd).resolve()
    for candidate in (folder, *folder.parents):
        for marker in VCS_MARKERS:
            if (candidate / marker).exists():
                raise SnapshotError(
                    "datum's working folder is inside a version-control working copy "
                    f"({candidate}), so it can't run safely. Set TMPDIR to a folder outside "
                    "it and restart the app."
                )


# --- snapshot ------------------------------------------------------------------------------


@dataclass(frozen=True)
class Snapshot:
    folder: Path
    config: Path
    lock: Path
    datasets: dict[str, str]  # id -> study-relative target
    lock_items: dict[str, dict[str, Any]]


def make_snapshot(
    study: Path,
    loaded: cfg.Loaded,
    folder: Path,
    ids: list[str] | None = None,
    *,
    seed: bool = True,
    targets_in: Path | None = None,
    neutral: bool = False,
) -> Snapshot:
    """Write the managed copy of the config and lock into ``folder``.

    Only the datasets in ``ids`` (all when None) are in the copy: datum substitutes every
    ``${NAME}`` in the config it loads, so a fetch of one dataset must not need another's
    credentials. The lockfile keeps the other datasets' entries as they are.

    ``targets_in`` is where targets point (default ``folder/files``). With ``seed``, the
    current target files of those datasets are copied there. With ``neutral``, every
    source is replaced by a missing local file: for commands that read only ids, policies
    and targets (``audit``, ``delete``, ``undelete``, ``unlock``), so they need no
    credentials and can reach nothing.
    """
    study = study.resolve()
    folder.mkdir(parents=True, exist_ok=True)
    files = targets_in or folder / FILES
    data = cfg.managed_copy(loaded.data)
    lock_items = read_lock(study / LOCK_FILE)
    targets: dict[str, str] = {}
    if ids is not None:
        data["datasets"] = [
            d for d in data.get("datasets") or [] if isinstance(d, dict) and d.get("id") in ids
        ]
    for ds in data.get("datasets") or []:
        if neutral:
            ds.pop("sources", None)
            ds["source"] = {"type": "file", "path": str(folder / "no-source")}
        did, target = str(ds.get("id")), ds.get("target")
        if cfg._target_problem(study, target) is not None:
            # Only an audit sees a config with problems; point it somewhere harmless.
            ds["target"] = str(files / ".invalid" / did)
            continue
        targets[did] = target
        ds["target"] = str(files / target)
        for _, source in cfg._sources(ds):
            if isinstance(source, dict) and source.get("type") == "file":
                path = str(source["path"])
                if not Path(path).is_absolute():
                    source["path"] = str(study / path)
        if seed:
            _seed(study, target, lock_items.get(did), files)
    snapshot_config = folder / SNAPSHOT_CONFIG
    snapshot_config.write_text(cfg.dump(data), "utf-8")
    snapshot_lock = folder / SNAPSHOT_LOCK
    if (study / LOCK_FILE).is_file():
        shutil.copyfile(study / LOCK_FILE, snapshot_lock)
    return Snapshot(folder, snapshot_config, snapshot_lock, targets, lock_items)


def _seed(study: Path, target: str, item: Mapping[str, Any] | None, files: Path) -> None:
    for rel in tracked_files(study, target, item):
        src = study_file(study, rel)
        if src.is_file() and not src.is_symlink():
            dest = files / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dest)


# --- datum's JSON --------------------------------------------------------------------------


NETWORK_HINTS = (
    "dial tcp",
    "no such host",
    "connection refused",
    "i/o timeout",
    "network is unreachable",
    "tls handshake timeout",
)
OFFLINE_HINT = (
    "The source could not be reached. https and git sources need the network; "
    "if this computer is offline, use a file source or try again when connected."
)


def _clean_result(raw: Any, redactor: Redactor) -> dict[str, Any] | None:
    if not isinstance(raw, dict) or not isinstance(raw.get("id"), str):
        return None
    status = raw.get("status")
    out: dict[str, Any] = {"id": raw["id"], "status": status if status in STATUSES else "error"}
    for key in ("message", "lock_fingerprint", "remote_fingerprint"):
        if isinstance(raw.get(key), str) and raw[key]:
            out[key] = redactor.text(raw[key])[:500]
    message = out.get("message", "")
    if out["status"] == "error" and any(h in message.lower() for h in NETWORK_HINTS):
        out["message"] = f"{OFFLINE_HINT} ({message})"[:500]
    warnings = raw.get("warnings")
    if isinstance(warnings, list):
        out["warnings"] = [redactor.text(str(w))[:500] for w in warnings[:20]]
    return out


def parse_results(text: str, redactor: Redactor) -> list[dict[str, Any]]:
    """datum's check/fetch ``results``, redacted. A top-level error raises DatumError."""
    doc = parse_json(text)
    if isinstance(doc, dict) and isinstance(doc.get("error"), str):
        raise DatumError("datum_error", redactor.text(doc["error"])[:500])
    results = doc.get("results") if isinstance(doc, dict) else None
    if not isinstance(results, list):
        raise DatumError("bad_output", "datum's answer had no results.")
    return [r for r in (_clean_result(x, redactor) for x in results) if r is not None]


def error_text(text: str, redactor: Redactor) -> str | None:
    try:
        doc = json.loads(text)
    except ValueError:
        return None
    if isinstance(doc, dict) and isinstance(doc.get("error"), str):
        return redactor.text(doc["error"])[:500]
    return None


# --- jobs ----------------------------------------------------------------------------------


def new_review_id() -> str:
    return secrets.token_hex(8)


def review_dir(study: Path, review_id: str) -> Path:
    if not REVIEW_ID_RE.match(review_id):
        raise SnapshotError("Unknown review.")
    return study / REVIEWS_DIR / review_id


@dataclass
class _Held:
    redactor: Redactor
    credentials: dict[str, str]

    def forget(self, job_dir: Path) -> None:
        for name in ("stdout.log", "stderr.log"):
            self.redactor.scrub_file(job_dir / name)
        self.credentials.clear()
        self.redactor.clear()


def _job_text(ctx: JobContext, held: _Held) -> str:
    for name in ("stdout.log", "stderr.log"):
        held.redactor.scrub_file(ctx.job_dir / name)
    try:
        return (ctx.job_dir / "stdout.log").read_text("utf-8", errors="replace")
    except OSError:
        return ""


def _diagnose(held: _Held) -> Callable[[JobContext, int], str | None]:
    def diagnose(ctx: JobContext, code: int) -> str | None:
        text = error_text(_job_text(ctx, held), held.redactor)
        if text:
            return f"datum stopped: {text}"
        if code == 2:
            return "datum could not read the external data settings."
        return None

    return diagnose


def _explain(code: int) -> str:
    return f"datum stopped with exit code {code}. The job log has details."


def _build(
    study: Path,
    loaded: cfg.Loaded,
    held: _Held,
    command: str,
    ids: list[str] | None,
    into_staging: bool,
) -> Callable[[JobContext], CommandSpec]:
    def build(ctx: JobContext) -> CommandSpec:
        problems = cfg.check(loaded.data, study).problems
        if problems:
            raise JobPrepareError(problems[0].text())
        try:
            check_cwd(ctx.cwd)
            exe = executable(ctx.runtime)
            base = ctx.staging if into_staging and ctx.staging is not None else ctx.cwd
            snap = make_snapshot(study, loaded, base, ids)
        except (SnapshotError, DatumError) as exc:
            raise JobPrepareError(exc.message) from None
        args = argv(ctx.runtime, snap.config, snap.lock, command, *(ids or []))
        return CommandSpec(args, datum_env(held.credentials), [exe.parent])

    return build


def check_request(
    study: Path, loaded: cfg.Loaded, credentials: Mapping[str, str] | None = None
) -> JobRequest:
    """The ``data_check`` job: report-only, even under the ``update`` policy.

    The snapshot lives in the job's isolated cwd, outside the study; the only study file
    the job writes is ``state.json`` (the last check).
    """
    study = study.resolve()
    held = _Held(Redactor((credentials or {}).values()), dict(credentials or {}))
    job = contract(CHECK_CAPABILITY)
    assert job.timeout_s is not None  # noqa: S101 - the descriptor sets one

    def review(ctx: JobContext) -> ReviewResult:
        try:
            results = parse_results(_job_text(ctx, held), held.redactor)
        except DatumError as exc:
            return ReviewResult(False, exc.message)
        code = _exit_code(results)
        doc = {
            "schema_version": "1.0",
            "kind": "check",
            "at": state.now(),
            "exit_code": code,
            "results": results,
        }
        problems = job.check_output(doc, "check results")
        if problems:
            return ReviewResult(False, problems[0])
        state.record_check(study, results)
        return ReviewResult(True)

    return JobRequest(
        kind="datum.check",
        label="Check external data for updates",
        build=_build(study, loaded, held, "check", None, into_staging=False),
        timeout_s=job.timeout_s,
        isolated_cwd=True,
        ok_exit_codes=(0, 1),
        review=review,
        diagnose=_diagnose(held),
        explain=_explain,
        cleanup=held.forget,
    )


def _exit_code(results: list[dict[str, Any]]) -> int:
    """datum's exit code, as its results imply it: 1 when anything changed or failed."""
    changed = ("updated", "fetched", "stale", "fail", "error")
    return 1 if any(r["status"] in changed for r in results) else 0


def fetch_request(
    study: Path,
    loaded: cfg.Loaded,
    ids: list[str],
    review_id: str,
    credentials: Mapping[str, str] | None = None,
) -> JobRequest:
    """The ``data_fetch`` job: datum fetches into a review folder; nothing else changes.

    datum writes straight into the job's output staging (``.opncafyr/datafetch/reviews/
    <id>.staging``); the job manager renames it to the review folder only after a clean
    exit and a passing review, so a review folder is always complete.
    """
    study = study.resolve()
    held = _Held(Redactor((credentials or {}).values()), dict(credentials or {}))
    job = contract(FETCH_CAPABILITY)
    assert job.timeout_s is not None  # noqa: S101 - the descriptor sets one
    old_lock = lock_sha256(study)

    def review(ctx: JobContext) -> ReviewResult:
        assert ctx.staging is not None  # noqa: S101 - the request has an output
        try:
            results = parse_results(_job_text(ctx, held), held.redactor)
            doc = build_review(study, loaded, ctx.staging, ids, results, review_id, old_lock)
        except (DatumError, SnapshotError) as exc:
            return ReviewResult(False, exc.message)
        doc["exit_code"] = _exit_code(results)
        problems = job.check_output(doc, REVIEW_DOCUMENT)
        if problems:
            return ReviewResult(False, problems[0])
        (ctx.staging / REVIEW_DOCUMENT).write_text(json.dumps(doc, indent=2), "utf-8")
        return ReviewResult(True)

    label = "Fetch external data" if len(ids) != 1 else f"Fetch external data ({ids[0]})"
    return JobRequest(
        kind="datum.fetch",
        label=label,
        build=_build(study, loaded, held, "fetch", ids, into_staging=True),
        timeout_s=job.timeout_s,
        isolated_cwd=True,
        output=f"{REVIEWS_DIR}/{review_id}",
        ok_exit_codes=(0, 1),
        review=review,
        diagnose=_diagnose(held),
        explain=_explain,
        cleanup=held.forget,
    )


def build_review(
    study: Path,
    loaded: cfg.Loaded,
    staging: Path,
    ids: list[str],
    results: list[dict[str, Any]],
    review_id: str,
    old_lock: str | None,
) -> dict[str, Any]:
    """What Apply would change, per dataset and file, from the snapshot after datum ran."""
    old_items = read_lock(study / LOCK_FILE)
    new_items = read_lock(staging / SNAPSHOT_LOCK)
    by_id = {r["id"]: r for r in results}
    targets = {d["id"]: d["target"] for d in loaded.datasets}
    datasets = []
    for did in ids:
        target = targets.get(did)
        if target is None:
            continue
        result = by_id.get(did, {"id": did, "status": "missing"})
        entry: dict[str, Any] = {**result, "target": target, "files": []}
        # datum's fetch output has no previous fingerprint: take it from the old lock.
        previous = (old_items.get(did) or {}).get("remote_fingerprint")
        if "lock_fingerprint" not in entry and isinstance(previous, str) and previous:
            entry["lock_fingerprint"] = previous
        if result["status"] in ("fetched", "updated", "ok"):
            entry["files"] = _file_actions(
                study, staging, target, old_items.get(did), new_items.get(did)
            )
        datasets.append(entry)
    return {
        "schema_version": "1.0",
        "kind": "fetch",
        "review_id": review_id,
        "at": state.now(),
        "exit_code": 0,
        "config_sha256": loaded.sha256,
        "lock_sha256": old_lock,
        "datasets": datasets,
    }


def _file_actions(
    study: Path,
    staging: Path,
    target: str,
    old: Mapping[str, Any] | None,
    new: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    new_paths = tracked_files(study, target, new)
    old_paths = tracked_files(study, target, old) if old else []
    if dir_paths(new) is None and dir_paths(old) is not None:
        old_paths = [p for p in old_paths if p != target]
    out = []
    for rel in new_paths:
        staged = staging / FILES / rel
        if not staged.is_file() or staged.is_symlink():
            continue
        new_size, new_sha = state.fingerprint(staged)
        cur = study_file(study, rel)
        cur_size, cur_sha = state.fingerprint(cur) if cur.is_file() else (None, None)
        action = "new" if cur_sha is None else ("unchanged" if cur_sha == new_sha else "replace")
        out.append(
            {
                "path": rel,
                "action": action,
                "current_sha256": cur_sha,
                "current_size": cur_size,
                "new_sha256": new_sha,
                "new_size": new_size,
            }
        )
    for rel in sorted(set(old_paths) - set(new_paths)):
        cur = study_file(study, rel)
        if not cur.is_file():
            continue
        cur_size, cur_sha = state.fingerprint(cur)
        out.append(
            {
                "path": rel,
                "action": "remove",
                "current_sha256": cur_sha,
                "current_size": cur_size,
                "new_sha256": None,
                "new_size": None,
            }
        )
    return out


# --- quick commands on a snapshot -----------------------------------------------------------


def audit(study: Path, loaded: cfg.Loaded, runtime: Any, scratch: Path) -> list[dict[str, Any]]:
    """``datum --json audit`` on a snapshot: config and lock only, offline, no writes."""
    check_cwd(scratch)
    snap = make_snapshot(study, loaded, scratch / "snapshot", seed=False, neutral=True)
    result = quick(runtime, argv(runtime, snap.config, snap.lock, "audit"), scratch / "cwd")
    doc = parse_json(result.stdout)
    if result.code != 0 or not isinstance(doc, dict):
        message = doc.get("error") if isinstance(doc, dict) else None
        raise DatumError("audit_failed", Redactor().text(str(message or "datum audit failed.")))
    entries = doc.get("entries")
    if not isinstance(entries, list):
        raise DatumError("bad_output", "datum's audit could not be read.")
    out = []
    for e in entries:
        if not isinstance(e, dict) or not isinstance(e.get("id"), str):
            continue
        out.append(
            {
                "id": e["id"],
                "status": str(e.get("status", "")),
                "in_config": bool(e.get("in_config")),
                "policy": e.get("policy"),
                "remote_fingerprint": e.get("remote_fingerprint"),
                "local_sha256": e.get("local_sha256"),
                "checked_at": e.get("checked_at"),
                "deleted_at": e.get("deleted_at"),
                "inaccessible_at": e.get("inaccessible_at"),
            }
        )
    return out


def lock_edit(
    study: Path,
    loaded: cfg.Loaded,
    runtime: Any,
    scratch: Path,
    command: str,
    dataset: str,
) -> bytes:
    """Run ``delete``/``undelete``/``unlock`` on a snapshot and return the new lock bytes.

    Targets point at an empty scratch folder, so ``delete`` removes nothing real; the
    caller moves the study's files to ``previous/`` itself.
    """
    if command not in ("delete", "undelete", "unlock"):
        raise ValueError(command)
    check_cwd(scratch)
    snap = make_snapshot(
        study,
        loaded,
        scratch / "snapshot",
        [dataset],
        seed=False,
        targets_in=scratch / "empty",
        neutral=True,
    )
    if not snap.lock.is_file():
        raise DatumError("no_lock", "Nothing has been fetched for this dataset yet.")
    args = argv(runtime, snap.config, snap.lock, command, dataset, yes=command != "undelete")
    result = quick(runtime, args, scratch / "cwd")
    if result.code != 0:
        raise DatumError(f"{command}_failed", f"datum could not {command} this dataset.")
    return snap.lock.read_bytes()
