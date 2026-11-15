"""Run datum so it never writes the study (#83, #85, ADR 0012).

datum 1.7.0 does the confinement itself (``argv``: ``--no-vcs``, ``--root``,
``--allow-types`` and the rest); this module adds what the launcher keeps around it:

1. **A fixed input.** The checked config (``cfg.managed_copy``: ``ignore: false``
   everywhere) and a copy of the study's lockfile are written to a launcher-owned folder,
   so datum reads exactly what was checked and the review knows which lock it started
   from. Targets and relative ``file`` sources resolve in the study through ``--root``.
2. **No writes.** A check is ``--report-only``. A fetch is staged: ``--stage-dir`` puts
   every new file under the review folder's ``files/<target>`` and ``--lock-out`` the new
   lockfile next to it. Jobs still get the job manager's ``isolated_cwd``.
3. **A review.** datum's ``--json`` output is redacted, checked and turned into a review
   document. A check stops there (it records the result in ``state.json`` only); a fetch
   leaves a review folder with the new files that ``promote.apply`` moves into place
   after the user confirms.
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
NO_SOURCE = "no-source"
# datum's check --report-only messages for an ``update`` dataset, and what the page says.
REPORT_ONLY = {
    "remote changed; would update (--report-only)": (
        "An update is available. Fetch it to review the change before it is applied."
    ),
    "target missing; would update (--report-only)": (
        "Not in this study yet. Fetch it to review the files before they are added."
    ),
}
DRIFT = ("ok", "changed", "missing", "unknown")  # audit --verify-local
LOCK_EDITS = {"delete": "deleted", "undelete": "undeleted", "unlock": "unlocked"}


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


def recorded_files(target: str, item: Mapping[str, Any] | None) -> list[tuple[str, str | None]]:
    """Study-relative paths and SHA-256s the lockfile records for a dataset (datum 1.7.0).

    A single-file target has its hash in ``local_sha256``; a directory has ``dir_files``.
    A directory fetched by datum 1.6 has only ``dir_paths``, so its hashes are ``None``.
    """
    if not item:
        return []
    files = item.get("dir_files")
    if isinstance(files, list):
        out: list[tuple[str, str | None]] = []
        for f in files:
            if isinstance(f, dict) and isinstance(f.get("path"), str):
                rel = dir_paths({"dir_paths": [f["path"]]})
                sha = f.get("sha256")
                out.append((f"{target}/{rel[0]}", sha if isinstance(sha, str) else None))
        return out
    paths = dir_paths(item)
    if paths is not None:
        return [(f"{target}/{p}", None) for p in paths]
    sha = item.get("local_sha256")
    return [(target, sha if isinstance(sha, str) else None)]


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


# --- snapshot ------------------------------------------------------------------------------


@dataclass(frozen=True)
class Snapshot:
    folder: Path
    config: Path
    lock: Path
    lock_sha256: str | None  # of the copy datum reads; None when the study has no lock


def make_snapshot(
    study: Path, loaded: cfg.Loaded, folder: Path, *, harmless: bool = False
) -> Snapshot:
    """Write the checked config and a copy of the study's lockfile into ``folder``.

    With ``harmless`` (``audit`` and lock edits only, which may see a config with
    problems), every dataset the policy refuses is reduced to its id with a missing local
    source and a target under ``.invalid/``, so one bad dataset doesn't make datum refuse
    the whole config and the others' lock status still shows.
    """
    study = study.resolve()
    folder.mkdir(parents=True, exist_ok=True)
    data = cfg.managed_copy(loaded.data)
    if harmless:
        data = _harmless(data, study, folder)
    snapshot_config = folder / SNAPSHOT_CONFIG
    snapshot_config.write_text(cfg.dump(data), "utf-8")
    snapshot_lock = folder / SNAPSHOT_LOCK
    snapshot_lock.unlink(missing_ok=True)
    lock_sha = None
    if (study / LOCK_FILE).is_file():
        shutil.copyfile(study / LOCK_FILE, snapshot_lock)
        lock_sha = state.fingerprint(snapshot_lock)[1]
    return Snapshot(folder, snapshot_config, snapshot_lock, lock_sha)


def _harmless(data: dict[str, Any], study: Path, folder: Path) -> dict[str, Any]:
    problems = cfg.check(data, study).problems
    bad = {p.dataset for p in problems if p.dataset is not None}
    defaults = data.get("defaults") if isinstance(data.get("defaults"), dict) else {}
    if defaults.get("policy") not in (None, *cfg.POLICIES):
        defaults.pop("policy")
    datasets, seen = [], set()
    for ds in data.get("datasets") if isinstance(data.get("datasets"), list) else []:
        did = ds.get("id") if isinstance(ds, dict) else None
        if not isinstance(did, str) or not cfg.ID_RE.match(did) or did in seen:
            continue
        seen.add(did)
        if did in bad:
            ds = {
                "id": did,
                "desc": "",
                "source": {"type": "file", "path": str(folder / NO_SOURCE)},
                "target": f".invalid/{did}",
                "ignore": False,
            }
        datasets.append(ds)
    data["datasets"] = datasets
    return data


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
    if out.get("message") in REPORT_ONLY:
        out["message"] = REPORT_ONLY[out["message"]]
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
    lock_sha256: str | None = None  # the lock the job's datum started from

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
) -> Callable[[JobContext], CommandSpec]:
    def build(ctx: JobContext) -> CommandSpec:
        checked = cfg.check(loaded.data, study)
        if checked.problems:
            raise JobPrepareError(checked.problems[0].text())
        try:
            exe = executable(ctx.runtime)
            snap = make_snapshot(study, loaded, ctx.cwd / "snapshot")
        except (SnapshotError, DatumError) as exc:
            raise JobPrepareError(exc.message) from None
        held.lock_sha256 = snap.lock_sha256
        if command == "fetch":
            assert ctx.staging is not None  # noqa: S101 - the request has an output
            extra = [
                "--stage-dir",
                str(ctx.staging / FILES),
                "--lock-out",
                str(ctx.staging / SNAPSHOT_LOCK),
            ]
        else:
            extra = ["--report-only"]
        args = argv(
            ctx.runtime,
            snap.config,
            snap.lock,
            command,
            *(ids or []),
            root=study,
            credentials=checked.credentials,
            extra=extra,
        )
        return CommandSpec(args, datum_env(held.credentials), [exe.parent])

    return build


def check_request(
    study: Path, loaded: cfg.Loaded, credentials: Mapping[str, str] | None = None
) -> JobRequest:
    """The ``data_check`` job: ``--report-only``, even under the ``update`` policy.

    datum reads the study's targets in place and writes nothing; the only study file the
    job writes is ``state.json`` (the last check).
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
        build=_build(study, loaded, held, "check", None),
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
    """The ``data_fetch`` job: a staged datum fetch into a review folder.

    ``--stage-dir`` and ``--lock-out`` point into the job's output staging
    (``.opncafyr/datafetch/reviews/<id>.staging``: ``files/<target>`` and ``lock.yaml``);
    the job manager renames it to the review folder only after a clean exit and a passing
    review, so a review folder is always complete. The study's targets and lockfile are
    only read.
    """
    study = study.resolve()
    held = _Held(Redactor((credentials or {}).values()), dict(credentials or {}))
    job = contract(FETCH_CAPABILITY)
    assert job.timeout_s is not None  # noqa: S101 - the descriptor sets one

    def review(ctx: JobContext) -> ReviewResult:
        assert ctx.staging is not None  # noqa: S101 - the request has an output
        try:
            results = parse_results(_job_text(ctx, held), held.redactor)
            doc = build_review(
                study, loaded, ctx.staging, ids, results, review_id, held.lock_sha256
            )
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
        build=_build(study, loaded, held, "fetch", ids),
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
    """What Apply would change, per dataset and file, from the staged fetch.

    ``old_lock`` is the SHA-256 of the lockfile datum started from; Apply refuses if the
    study's lockfile is no longer that one.
    """
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


# --- quick commands -----------------------------------------------------------------------


def audit(study: Path, loaded: cfg.Loaded, runtime: Any, scratch: Path) -> list[dict[str, Any]]:
    """``datum --verify-local --json audit``: config and lock, plus a hash of each target.

    Offline and read-only. ``drift`` says whether the study's files still match what the
    lockfile recorded (``ok``, ``changed``, ``missing`` or ``unknown``).
    """
    study = study.resolve()
    snap = make_snapshot(study, loaded, scratch / "snapshot", harmless=True)
    args = argv(
        runtime,
        snap.config,
        snap.lock,
        "audit",
        root=study,
        credentials=cfg.check(loaded.data, study).credentials,
        confine=False,
        extra=["--verify-local"],
    )
    result = quick(runtime, args, scratch / "cwd")
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
        drift = e.get("drift")
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
                "drift": drift if drift in DRIFT else None,
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
    """Run ``delete``/``undelete``/``unlock`` on a lock copy and return the new lock bytes.

    ``--root`` is an empty scratch folder, so ``delete`` removes nothing real; the caller
    moves the study's files to ``previous/`` itself.
    """
    if command not in LOCK_EDITS:
        raise ValueError(command)
    study = study.resolve()
    snap = make_snapshot(study, loaded, scratch / "snapshot", harmless=True)
    if not snap.lock.is_file():
        raise DatumError("no_lock", "Nothing has been fetched for this dataset yet.")
    empty = scratch / "empty"
    empty.mkdir(parents=True, exist_ok=True)
    args = argv(
        runtime,
        snap.config,
        snap.lock,
        command,
        dataset,
        root=empty,
        credentials=cfg.check(loaded.data, study).credentials,
        confine=False,
        yes=True,
    )
    result = quick(runtime, args, scratch / "cwd")
    redactor = Redactor()
    try:
        doc = parse_json(result.stdout)
    except DatumError:
        doc = None
    if isinstance(doc, dict) and isinstance(doc.get("error"), str):
        raise DatumError(f"{command}_failed", redactor.text(doc["error"])[:500])
    results = doc.get("results") if isinstance(doc, dict) else None
    mine = [r for r in results or [] if isinstance(r, dict) and r.get("id") == dataset]
    if result.code != 0 or not mine or mine[0].get("status") != LOCK_EDITS[command]:
        message = mine[0].get("message") if mine else None
        detail = f" ({redactor.text(message)[:300]})" if isinstance(message, str) else ""
        raise DatumError(f"{command}_failed", f"datum could not {command} this dataset{detail}.")
    return snap.lock.read_bytes()
