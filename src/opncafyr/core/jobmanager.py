"""Job manager: persisted, cancellable, noninteractive jobs (#12).

Adapters and workflows describe a job with a ``JobRequest``. The manager owns everything
around the command:

- state on disk under ``<workspace>/.opncafyr/jobs/<job-id>/``: ``state.json`` (schema
  ``job-v1``, written atomically), ``events.jsonl`` (messages written for a person),
  ``stdout.log``/``stderr.log`` (raw output, which never reaches messages, API responses
  or the launcher's own log), and ``diagnostic.json`` when a job fails or is cancelled;
- the state machine in ``core.jobs``;
- an argv-only spawn with an allow-listed environment and a job-owned TMPDIR;
- a timeout that every request must set (deckifyr's jobs and quartifyr's Quarto call
  have none of their own);
- process-tree cancellation;
- outputs written to ``<target>.staging/`` and renamed into place only after a clean exit
  and a passing review, so an incomplete directory never looks like a result;
- a concurrency limit per pool (default 1, so one R job at a time);
- a sweep when a workspace is opened: jobs left non-terminal by a launcher that died are
  marked ``failed``, their recorded process tree is killed, and staging is not promoted.

Jobs run on worker threads and need no connected browser. Only trusted Python code
submits jobs; there is no HTTP endpoint for it.
"""

from __future__ import annotations

import contextlib
import json
import logging
import math
import os
import re
import secrets
import shutil
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, Any

import psutil

from opncafyr.core.jobs import TERMINAL_STATES, JobState, transition
from opncafyr.core.manifest import atomic_write_json, validate
from opncafyr.core.proctree import PIPE, OwnedProcess, child_env, kill_recorded_tree, spawn
from opncafyr.core.runtime import Runtime, RuntimeUnavailable
from opncafyr.core.workspace import WorkspaceAccessError, resolve_in_root
from opncafyr.paths import WORKSPACE_META_DIR

log = logging.getLogger(__name__)

JOB_SCHEMA = "job-v1"
JOB_ID_RE = re.compile(r"^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{6}$")
_KIND_RE = re.compile(r"^[a-z][a-z0-9_.-]*$")
_POOL_RE = re.compile(r"^[a-z][a-z0-9_-]*$")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")

PROGRESS_PREFIX = "OPNCAFYR-PROGRESS: "
STAGING_SUFFIX = ".staging"
OWNER_FILE = ".opncafyr-job"
DEFAULT_POOL = "default"
DEFAULT_POOL_LIMIT = 1
MAX_MESSAGE = 300

# Fields of state.json that the API may show.
PUBLIC_FIELDS = (
    "job_id",
    "kind",
    "label",
    "pool",
    "state",
    "message",
    "created_at",
    "updated_at",
    "started_at",
    "finished_at",
    "timeout_s",
    "output",
    "exit_code",
    "reason",
)


class JobManagerError(RuntimeError):
    """The manager can't do what was asked (no workspace, jobs still active, ...)."""


class JobPrepareError(RuntimeError):
    """Raised by an adapter's ``build`` with a message written for a person."""


@dataclass(frozen=True)
class CommandSpec:
    """The command a job runs. ``argv[0]`` is an absolute path from the pinned runtime."""

    argv: list[str]
    env: dict[str, str] = field(default_factory=dict)
    path_dirs: list[Path] = field(default_factory=list)


@dataclass(frozen=True)
class ReviewResult:
    ok: bool
    message: str = ""


@dataclass(frozen=True)
class JobContext:
    """What a request's callables may use. Every path is already resolved."""

    job_id: str
    workspace: Path
    job_dir: Path
    cwd: Path
    tmpdir: Path
    staging: Path | None
    runtime: Runtime


@dataclass(frozen=True)
class JobRequest:
    """A job as described by a trusted adapter or workflow.

    ``output`` and ``cwd`` are workspace-relative. ``build`` returns the command; the
    optional ``review`` checks the staged outputs after a clean exit; ``progress`` turns a
    stdout line into a message (default: lines starting with ``PROGRESS_PREFIX``);
    ``explain`` turns a non-zero exit code into a message. None of these callables are
    persisted.
    """

    kind: str
    label: str
    build: Callable[[JobContext], CommandSpec]
    timeout_s: float
    pool: str = DEFAULT_POOL
    output: str | None = None
    cwd: str | None = None
    review: Callable[[JobContext], ReviewResult] | None = None
    progress: Callable[[str], str | None] | None = None
    explain: Callable[[int], str] | None = None

    def check(self) -> None:
        if not _KIND_RE.match(self.kind):
            raise ValueError(f"Invalid job kind {self.kind!r}.")
        if not _POOL_RE.match(self.pool):
            raise ValueError(f"Invalid job pool {self.pool!r}.")
        if not self.label.strip():
            raise ValueError("A job needs a label.")
        if not (isinstance(self.timeout_s, int | float) and math.isfinite(self.timeout_s)):
            raise ValueError("A job needs a finite timeout.")
        if self.timeout_s <= 0:
            raise ValueError("A job's timeout must be positive.")


@dataclass
class _Job:
    record: dict[str, Any]
    dir: Path
    request: JobRequest | None = None  # None for jobs read back from disk
    workspace: Path | None = None
    cancel_event: threading.Event = field(default_factory=threading.Event)
    cancel_reason: str = "cancelled"
    cancel_message: str | None = None
    thread: threading.Thread | None = None
    seq: int = 0

    @property
    def id(self) -> str:
        return self.record["job_id"]

    @property
    def state(self) -> JobState:
        return JobState(self.record["state"])

    @property
    def label(self) -> str:
        return self.record["label"]


class _Stop(Exception):
    """Ends a job early with a terminal state and message."""

    def __init__(self, state: JobState, reason: str, message: str) -> None:
        super().__init__(message)
        self.state = state
        self.reason = reason
        self.message = message


class JobManager:
    def __init__(
        self,
        runtime: Runtime | None = None,
        pool_limits: Mapping[str, int] | None = None,
        poll_interval: float = 0.1,
    ) -> None:
        self.runtime = runtime if runtime is not None else Runtime()
        self.pool_limits = dict(pool_limits or {})
        self.poll_interval = poll_interval
        self._lock = threading.RLock()
        self._jobs: dict[str, _Job] = {}
        self._queue: list[str] = []
        self._running: dict[str, int] = {}
        self._workspace: Path | None = None
        self._accepting = True

    # --- workspace -------------------------------------------------------------------

    @property
    def workspace(self) -> Path | None:
        return self._workspace

    def _jobs_root(self, workspace: Path) -> Path:
        return workspace / WORKSPACE_META_DIR / "jobs"

    def open(self, workspace_root: str | Path) -> list[str]:
        """Use this workspace's job folder. Returns the ids the sweep marked failed."""
        root = Path(workspace_root).resolve(strict=True)
        if not (root / WORKSPACE_META_DIR).is_dir():
            raise JobManagerError("This folder is not an opncafyr workspace.")
        with self._lock:
            if self.active():
                raise JobManagerError("Jobs are still running in the current workspace.")
            jobs_root = self._jobs_root(root)
            jobs_root.mkdir(exist_ok=True)
            self._workspace = root
            self._jobs = {}
            self._queue = []
            self._accepting = True
            swept: list[str] = []
            for entry in sorted(jobs_root.iterdir()):
                if not entry.is_dir() or not JOB_ID_RE.match(entry.name):
                    continue
                job = self._load(entry)
                if job is None:
                    continue
                self._jobs[job.id] = job
                if job.state not in TERMINAL_STATES:
                    self._sweep(job)
                    swept.append(job.id)
            return swept

    def _load(self, job_dir: Path) -> _Job | None:
        try:
            record = json.loads((job_dir / "state.json").read_text("utf-8"))
            validate(record, JOB_SCHEMA)
        except (OSError, ValueError) as exc:
            log.warning("Skipping unreadable job record %s (%s)", job_dir.name, type(exc).__name__)
            return None
        if record["job_id"] != job_dir.name:
            log.warning("Skipping job record %s: id does not match its folder", job_dir.name)
            return None
        job = _Job(record=record, dir=job_dir, workspace=self._workspace)
        events = job_dir / "events.jsonl"
        if events.is_file():
            with events.open("rb") as fh:
                job.seq = sum(1 for _ in fh)
        return job

    def _sweep(self, job: _Job) -> None:
        was = job.state
        pid, created = job.record.get("pid"), job.record.get("pid_create_time")
        killed = False
        if pid is not None and created is not None:
            killed = kill_recorded_tree(pid, created)
        if was is JobState.QUEUED:
            message = f"The launcher stopped before the {job.label} job started. Run it again."
        else:
            message = (
                f"The launcher stopped while the {job.label} job was running. "
                "Nothing it produced was kept; run it again."
            )
        self._finish(
            job,
            JobState.FAILED,
            message,
            reason="launcher_restarted",
            diagnostic={"state_at_stop": str(was), "orphan_process_killed": killed},
        )
        log.info("job %s marked failed after a launcher restart", job.id)

    # --- queries ---------------------------------------------------------------------

    def active(self) -> bool:
        with self._lock:
            return any(job.state not in TERMINAL_STATES for job in self._jobs.values())

    def active_count(self) -> int:
        with self._lock:
            return sum(job.state not in TERMINAL_STATES for job in self._jobs.values())

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            jobs = sorted(self._jobs.values(), key=lambda j: j.record["created_at"], reverse=True)
            return [_public(j.record) for j in jobs]

    def get(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            return _public(self._job(job_id).record)

    def events(self, job_id: str, after: int = 0) -> list[dict[str, Any]]:
        with self._lock:
            path = self._job(job_id).dir / "events.jsonl"
            if not path.is_file():
                return []
            out = []
            for line in path.read_text("utf-8").splitlines():
                with contextlib.suppress(ValueError):
                    event = json.loads(line)
                    if event.get("seq", 0) > after:
                        out.append(event)
            return out

    def _job(self, job_id: str) -> _Job:
        job = self._jobs.get(job_id)
        if job is None:
            raise KeyError(job_id)
        return job

    # --- commands --------------------------------------------------------------------

    def submit(self, request: JobRequest) -> str:
        request.check()
        with self._lock:
            if self._workspace is None:
                raise JobManagerError("Open a workspace before starting a job.")
            if not self._accepting:
                raise JobManagerError("The launcher is stopping and accepts no new jobs.")
            jobs_root = self._jobs_root(self._workspace)
            while True:
                job_id = _new_job_id()
                job_dir = jobs_root / job_id
                if not job_dir.exists():
                    break
            job_dir.mkdir()
            now = _now()
            record: dict[str, Any] = {
                "schema_version": 1,
                "job_id": job_id,
                "kind": request.kind,
                "label": request.label,
                "pool": request.pool,
                "state": str(JobState.QUEUED),
                "message": f"Waiting to start: {request.label}.",
                "created_at": now,
                "updated_at": now,
                "started_at": None,
                "finished_at": None,
                "timeout_s": request.timeout_s,
                "output": request.output,
                "pid": None,
                "pid_create_time": None,
                "exit_code": None,
                "reason": None,
            }
            job = _Job(record=record, dir=job_dir, request=request, workspace=self._workspace)
            self._jobs[job_id] = job
            self._write(job)
            self._event(job, "info", record["message"])
            self._queue.append(job_id)
            log.info("job %s queued (%s)", job_id, request.kind)
            self._dispatch()
            return job_id

    def cancel(
        self, job_id: str, *, reason: str = "cancelled", message: str | None = None
    ) -> JobState:
        """Cancel a job. A queued job stops at once; a running one is killed by its worker."""
        with self._lock:
            job = self._job(job_id)
            if job.state in TERMINAL_STATES:
                return job.state
            if job.thread is None:
                with contextlib.suppress(ValueError):
                    self._queue.remove(job_id)
                self._finish(
                    job,
                    JobState.CANCELLED,
                    message or f"The {job.label} job was cancelled before it started.",
                    reason=reason,
                )
                return job.state
            job.cancel_reason = reason
            job.cancel_message = message
            job.cancel_event.set()
            return job.state

    def wait(self, job_id: str, timeout: float | None = None) -> JobState:
        """Block until the job is terminal (for tests and shutdown)."""
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            with self._lock:
                job = self._job(job_id)
                if job.state in TERMINAL_STATES:
                    return job.state
            if deadline is not None and time.monotonic() >= deadline:
                return job.state
            time.sleep(0.05)

    def stop_accepting(self) -> None:
        """Refuse new jobs. Queued and running ones carry on (Quit after jobs, #14)."""
        with self._lock:
            self._accepting = False

    def resume_accepting(self) -> None:
        with self._lock:
            self._accepting = True

    def shutdown(self, timeout: float = 30.0) -> None:
        """Stop accepting jobs and cancel every live one.

        The lifecycle (``core/lifecycle.py``) only stops the launcher with no live jobs,
        or after the user chose to cancel them, so this cancel is the backstop.
        """
        with self._lock:
            self._accepting = False
            live = [j for j in self._jobs.values() if j.state not in TERMINAL_STATES]
            for job in live:
                self.cancel(
                    job.id,
                    reason="launcher_stopped",
                    message=f"The launcher was stopped, so the {job.label} job was cancelled.",
                )
            threads = [j.thread for j in live if j.thread is not None]
        deadline = time.monotonic() + timeout
        for thread in threads:
            thread.join(max(0.0, deadline - time.monotonic()))

    # --- scheduling ------------------------------------------------------------------

    def _dispatch(self) -> None:
        """Start queued jobs whose pool has a free slot. Caller holds the lock."""
        for job_id in list(self._queue):
            job = self._jobs[job_id]
            pool = job.record["pool"]
            if self._running.get(pool, 0) >= self.pool_limits.get(pool, DEFAULT_POOL_LIMIT):
                continue
            self._queue.remove(job_id)
            self._running[pool] = self._running.get(pool, 0) + 1
            job.thread = threading.Thread(
                target=self._worker, args=(job,), name=f"job-{job_id}", daemon=True
            )
            job.thread.start()

    def _worker(self, job: _Job) -> None:
        state, reason = JobState.COMPLETED, None
        message = f"Completed: {job.label}."
        try:
            self._execute(job)
        except _Stop as stop:
            state, message, reason = stop.state, stop.message, stop.reason
        except Exception as exc:  # noqa: BLE001 - never leave a job non-terminal
            log.error("job %s internal error: %s", job.id, type(exc).__name__)
            state, reason = JobState.FAILED, "internal_error"
            message = f"The {job.label} job stopped because of an internal error in the launcher."
        try:
            # Clean up before the terminal state is written, so terminal means finished.
            for name in ("tmp", "work"):
                _remove_tree(job.dir / name)
        finally:
            self._finish(job, state, message, reason=reason)
            with self._lock:
                pool = job.record["pool"]
                self._running[pool] = max(0, self._running.get(pool, 0) - 1)
                self._dispatch()

    # --- one job ---------------------------------------------------------------------

    def _check_cancel(self, job: _Job) -> None:
        if job.cancel_event.is_set():
            raise _Stop(JobState.CANCELLED, job.cancel_reason, self._cancel_text(job))

    def _cancel_text(self, job: _Job) -> str:
        return job.cancel_message or (
            f"The {job.label} job was cancelled. Nothing it produced was kept."
        )

    def _execute(self, job: _Job) -> None:
        request = job.request
        workspace = job.workspace
        assert request is not None and workspace is not None  # noqa: S101 - submitted jobs only
        self._check_cancel(job)
        self._set(job, JobState.PREPARING, f"Preparing: {job.label}.")
        ctx, target = self._prepare(job, request, workspace)
        try:
            spec = request.build(ctx)
        except (JobPrepareError, RuntimeUnavailable) as exc:
            raise _Stop(JobState.FAILED, "prepare_failed", f"{job.label}: {exc}") from None
        except Exception as exc:  # noqa: BLE001 - adapter bug; keep its details out
            log.error("job %s build failed: %s", job.id, type(exc).__name__)
            raise _Stop(
                JobState.FAILED, "prepare_failed", f"The {job.label} job could not be prepared."
            ) from None
        self._check_cancel(job)

        exit_code = self._run(job, request, ctx, spec)
        if exit_code != 0:
            text = request.explain(exit_code) if request.explain else None
            raise _Stop(
                JobState.FAILED,
                "exit_code",
                text
                or f"The {job.label} job failed (exit code {exit_code}). The job log has details.",
            )

        self._check_cancel(job)
        self._set(job, JobState.REVIEWING, f"Checking the results of {job.label}.")
        if request.review is not None:
            try:
                result = request.review(ctx)
            except Exception as exc:  # noqa: BLE001 - a broken review never passes
                log.error("job %s review raised %s", job.id, type(exc).__name__)
                result = ReviewResult(False, "The results could not be checked.")
            if not result.ok:
                raise _Stop(
                    JobState.FAILED,
                    "review_failed",
                    f"The {job.label} job finished, but its results did not pass the check: "
                    f"{result.message or 'no reason given'}",
                )
        self._check_cancel(job)
        if ctx.staging is not None and target is not None:
            try:
                (ctx.staging / OWNER_FILE).unlink(missing_ok=True)
                if target.exists():
                    raise FileExistsError(target)
                os.replace(ctx.staging, target)
            except OSError:
                raise _Stop(
                    JobState.FAILED,
                    "promote_failed",
                    f"The {job.label} job finished, but its results could not be moved into "
                    f"{request.output}.",
                ) from None

    def _prepare(
        self, job: _Job, request: JobRequest, workspace: Path
    ) -> tuple[JobContext, Path | None]:
        try:
            target = resolve_in_root(workspace, request.output) if request.output else None
            cwd = resolve_in_root(workspace, request.cwd) if request.cwd else job.dir / "work"
        except WorkspaceAccessError:
            raise _Stop(
                JobState.FAILED,
                "prepare_failed",
                f"The {job.label} job asked for a folder outside the workspace.",
            ) from None
        staging = None
        if target is not None:
            if target.exists():
                raise _Stop(
                    JobState.FAILED,
                    "prepare_failed",
                    f"The output folder {request.output} already exists. "
                    "Completed outputs are never replaced.",
                )
            staging = target.with_name(target.name + STAGING_SUFFIX)
            self._claim_staging(job, staging, request.output or "")
        cwd.mkdir(parents=True, exist_ok=True)
        tmpdir = job.dir / "tmp"
        tmpdir.mkdir(exist_ok=True)
        ctx = JobContext(
            job_id=job.id,
            workspace=workspace,
            job_dir=job.dir,
            cwd=cwd,
            tmpdir=tmpdir,
            staging=staging,
            runtime=self.runtime,
        )
        return ctx, target

    def _claim_staging(self, job: _Job, staging: Path, output: str) -> None:
        if staging.exists():
            marker = staging / OWNER_FILE
            owner_id = marker.read_text("utf-8").strip() if marker.is_file() else None
            with self._lock:
                owner = self._jobs.get(owner_id) if owner_id else None
                stale = owner is not None and owner is not job and owner.state in TERMINAL_STATES
            if not stale:
                raise _Stop(
                    JobState.FAILED,
                    "prepare_failed",
                    f"A staging folder for {output} is in use or was left by an unknown job. "
                    "Remove it or choose another output folder.",
                )
            _remove_tree(staging)
        staging.mkdir(parents=True)
        (staging / OWNER_FILE).write_text(job.id, "utf-8")

    def _run(self, job: _Job, request: JobRequest, ctx: JobContext, spec: CommandSpec) -> int:
        tmp = str(ctx.tmpdir)
        env = child_env({**spec.env, "TMPDIR": tmp, "TMP": tmp, "TEMP": tmp}, spec.path_dirs)
        with (job.dir / "stderr.log").open("ab") as err:
            try:
                proc = spawn(spec.argv, env=env, cwd=ctx.cwd, stdout=PIPE, stderr=err)
            except (OSError, ValueError) as exc:
                log.error("job %s could not start: %s", job.id, type(exc).__name__)
                raise _Stop(
                    JobState.FAILED,
                    "spawn_failed",
                    f"The {job.label} job could not start its program.",
                ) from None
        try:
            created = psutil.Process(proc.pid).create_time()
        except psutil.Error:
            created = None
        with self._lock:
            job.record["pid"] = proc.pid
            job.record["pid_create_time"] = created
            job.record["started_at"] = _now()
        self._set(job, JobState.RUNNING, f"Running: {job.label}.")
        pump = threading.Thread(
            target=self._pump, args=(job, request, proc), name=f"job-{job.id}-out", daemon=True
        )
        pump.start()

        outcome = self._wait(job, request, proc)
        if outcome == "timeout":
            log.info("job %s timed out", job.id)
        pump.join(timeout=10)
        code = proc.popen.returncode
        with self._lock:
            job.record["exit_code"] = code
        if outcome == "cancelled":
            raise _Stop(JobState.CANCELLED, job.cancel_reason, self._cancel_text(job))
        if outcome == "timeout":
            raise _Stop(
                JobState.FAILED,
                "timeout",
                f"The {job.label} job ran longer than its {_duration(request.timeout_s)} limit "
                "and was stopped. Nothing it produced was kept.",
            )
        return code if code is not None else -1

    def _wait(self, job: _Job, request: JobRequest, proc: OwnedProcess) -> str:
        """Wait for exit, cancel or timeout; then kill whatever is left of the tree."""
        deadline = time.monotonic() + request.timeout_s
        known: dict[int, psutil.Process] = {}
        next_scan = 0.0
        outcome = "exited"
        try:
            while proc.poll() is None:
                now = time.monotonic()
                if job.cancel_event.is_set():
                    outcome = "cancelled"
                    break
                if now >= deadline:
                    outcome = "timeout"
                    break
                if now >= next_scan:
                    # Remember descendants: once their parent exits they are reparented
                    # and can no longer be found by walking the tree.
                    for p in proc.descendants():
                        known.setdefault(p.pid, p)
                    next_scan = now + 0.5
                job.cancel_event.wait(self.poll_interval)
        finally:
            proc.kill_tree(known=list(known.values()))
        return outcome

    def _pump(self, job: _Job, request: JobRequest, proc: OwnedProcess) -> None:
        stream: IO[bytes] | None = proc.popen.stdout
        if stream is None:
            return
        parse = request.progress or _default_progress
        with (job.dir / "stdout.log").open("ab") as out:
            for raw in iter(stream.readline, b""):
                out.write(raw)
                out.flush()
                try:
                    text = parse(raw.decode("utf-8", "replace").rstrip("\r\n"))
                except Exception:  # noqa: BLE001 - a bad parser must not stop the log
                    text = None
                if text:
                    with self._lock:
                        if job.state is JobState.RUNNING:
                            job.record["message"] = _clean(text)
                            job.record["updated_at"] = _now()
                            self._write(job)
                            self._event(job, "info", job.record["message"])
        stream.close()

    # --- persistence -----------------------------------------------------------------

    def _set(self, job: _Job, state: JobState, message: str) -> None:
        with self._lock:
            job.record["state"] = str(transition(job.state, state))
            job.record["message"] = _clean(message)
            job.record["updated_at"] = _now()
            self._write(job)
            self._event(job, "info", job.record["message"])
        log.info("job %s %s", job.id, state)

    def _finish(
        self,
        job: _Job,
        state: JobState,
        message: str,
        *,
        reason: str | None = None,
        diagnostic: Mapping[str, Any] | None = None,
    ) -> None:
        with self._lock:
            if job.state in TERMINAL_STATES:
                return
            before = job.state
            now = _now()
            job.record["state"] = str(transition(before, state))
            job.record["message"] = _clean(message)
            job.record["updated_at"] = now
            job.record["finished_at"] = now
            job.record["reason"] = reason
            self._write(job)
            level = "info" if state is JobState.COMPLETED else "error"
            if state is JobState.CANCELLED:
                level = "warning"
            self._event(job, level, job.record["message"])
            if state is not JobState.COMPLETED:
                self._write_diagnostic(job, before, diagnostic or {})
        log.info("job %s %s (%s)", job.id, state, reason or "ok")

    def _write_diagnostic(self, job: _Job, before: JobState, extra: Mapping[str, Any]) -> None:
        output = job.record.get("output")
        staging = f"{output}{STAGING_SUFFIX}" if output else None
        if staging and job.workspace is not None:
            with contextlib.suppress(WorkspaceAccessError, OSError):
                if not resolve_in_root(job.workspace, staging).exists():
                    staging = None
        doc = {
            "job_id": job.id,
            "kind": job.record["kind"],
            "state": job.record["state"],
            "state_before": str(before),
            "reason": job.record["reason"],
            "message": job.record["message"],
            "exit_code": job.record.get("exit_code"),
            "at": job.record["finished_at"],
            "staging_kept": staging,
            "logs": ["stdout.log", "stderr.log"],
            **extra,
        }
        atomic_write_json(job.dir / "diagnostic.json", doc)

    def _write(self, job: _Job) -> None:
        atomic_write_json(job.dir / "state.json", job.record, JOB_SCHEMA)

    def _event(self, job: _Job, level: str, message: str) -> None:
        job.seq += 1
        event = {
            "seq": job.seq,
            "at": _now(),
            "state": job.record["state"],
            "level": level,
            "message": message,
        }
        with (job.dir / "events.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, sort_keys=True) + "\n")
            fh.flush()
            os.fsync(fh.fileno())


def _default_progress(line: str) -> str | None:
    if line.startswith(PROGRESS_PREFIX):
        return line[len(PROGRESS_PREFIX) :].strip() or None
    return None


def _clean(text: str) -> str:
    text = _CONTROL_RE.sub(" ", text).strip()
    return text if len(text) <= MAX_MESSAGE else text[: MAX_MESSAGE - 1] + "…"


def _public(record: Mapping[str, Any]) -> dict[str, Any]:
    return {k: record.get(k) for k in PUBLIC_FIELDS}


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _new_job_id() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + secrets.token_hex(3)


def _duration(seconds: float) -> str:
    if seconds < 120:
        n = round(seconds)
        return f"{n}-second"
    if seconds < 7200:
        return f"{round(seconds / 60)}-minute"
    return f"{round(seconds / 3600)}-hour"


def _remove_tree(path: Path) -> None:
    for _ in range(5):  # Windows may hold files briefly after a kill
        shutil.rmtree(path, ignore_errors=True)
        if not path.exists():
            return
        time.sleep(0.2)


__all__ = [
    "JOB_ID_RE",
    "PROGRESS_PREFIX",
    "CommandSpec",
    "JobContext",
    "JobManager",
    "JobManagerError",
    "JobPrepareError",
    "JobRequest",
    "ReviewResult",
]
