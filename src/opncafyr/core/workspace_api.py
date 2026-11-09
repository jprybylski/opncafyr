"""Workspace selection API (#17).

The folder comes from the native picker, which the controller runs (``core.picker``). The
browser never names an arbitrary folder: inspect, init and open accept only a folder
picked in this session or one on the recently-used list. Every POST also needs the
session secret (``core.security``). Paths travel in JSON bodies, never in URLs.

In cloud container mode (#42) the study folder is the one mounted when the container
started (``opncafyr launch --workspace``). There is no picker: "choosing" a folder returns
that one, and no other folder is accepted. Every folder, in either mode, must be on a
filesystem that replaces files atomically (``core.storage``).
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import anyio.to_thread
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from opncafyr import __version__
from opncafyr.core.artifacts import list_publications, list_runs
from opncafyr.core.jobmanager import JobManager, JobManagerError
from opncafyr.core.picker import PickerError, PickerUnavailable
from opncafyr.core.storage import check_storage
from opncafyr.core.workspace import (
    MISSING,
    RecentWorkspaces,
    Workspace,
    WorkspaceError,
    init_workspace,
    inspect_folder,
    open_workspace,
)

PICK_PROMPT = "Choose or create a study folder"

Picker = Callable[[str, Path | None], Path | None]


class PathBody(BaseModel):
    path: str


class OpenBody(PathBody):
    migrate: bool = False


def _error(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


def _canonical(path: str) -> str:
    try:
        return str(Path(path).resolve())
    except (OSError, ValueError):
        raise _error(400, "invalid_path", "That path can't be used.") from None


def mount_workspace_api(
    app: FastAPI, picker: Picker, recent: RecentWorkspaces, fixed: Path | None = None
) -> None:
    picker_lock = threading.Lock()
    open_lock = threading.Lock()
    picked: set[str] = set()
    fixed_path = str(Path(fixed).resolve()) if fixed is not None else None

    def approved(path: str) -> str:
        canonical = _canonical(path)
        if fixed_path is not None:
            if canonical != fixed_path:
                raise _error(
                    403,
                    "not_approved",
                    "This opncafyr serves only the study folder it was started with.",
                )
            return canonical
        if canonical not in picked and canonical not in recent.paths():
            raise _error(403, "not_approved", "Choose this folder with the folder picker first.")
        return canonical

    def storage_ok(path: str) -> dict[str, Any] | None:
        """Refuse a folder whose filesystem can't replace files atomically (#42)."""
        if not Path(path).is_dir():
            return None  # init/open report the missing folder themselves
        result = check_storage(Path(path))
        if not result.ok:
            raise _error(400, "unsupported_filesystem", result.message or "Unsupported storage.")
        if result.message:
            return {"kind": result.kind, "fstype": result.fstype, "message": result.message}
        return None

    def activate(workspace: Workspace, storage: dict[str, Any] | None) -> dict[str, Any]:
        jobs: JobManager = app.state.jobs
        try:
            swept = jobs.open(workspace.root)
        except JobManagerError as exc:
            raise _error(409, "jobs_active", str(exc)) from None
        lease = getattr(app.state, "workspace_lease", None)
        if lease is not None and lease.holder is not None:
            raise _error(409, "workspace_busy", f"{lease.holder} is changing workspace files.")
        app.state.workspace = workspace
        # A child serving the previous folder must not keep serving it (#56).
        sup = getattr(app.state, "supervisor", None)
        if sup is not None:
            sup.stop_bound_elsewhere(workspace.root)
        recent.remember(workspace.root)
        return {
            "workspace": workspace.summary(),
            "migrated_from": workspace.migrated_from,
            "backups": [p.relative_to(workspace.root).as_posix() for p in workspace.backups],
            "swept_jobs": swept,
            "storage": storage,
        }

    def workspace_error(exc: WorkspaceError) -> HTTPException:
        status = 409 if exc.code in ("needs_migration", "newer", "exists") else 400
        if exc.code == "permission":
            status = 403
        return _error(status, exc.code, exc.message)

    def check_jobs_idle() -> None:
        jobs: JobManager = app.state.jobs
        if jobs.active():
            raise _error(409, "jobs_active", "Jobs are still running in the current workspace.")

    @app.get("/api/workspace")
    def current() -> dict[str, Any]:
        ws: Workspace | None = app.state.workspace
        if fixed_path is not None:
            return {
                "workspace": ws.summary() if ws else None,
                "fixed": {"path": fixed_path, "name": Path(fixed_path).name},
                "recent": [],
            }
        return {
            "workspace": ws.summary() if ws else None,
            "fixed": None,
            "recent": [
                {
                    "path": entry["path"],
                    "name": Path(entry["path"]).name,
                    "opened_at": entry["opened_at"],
                    "available": Path(entry["path"]).is_dir(),
                }
                for entry in recent.load()
            ],
        }

    @app.get("/api/workspace/artifacts")
    def artifacts() -> dict[str, Any]:
        ws: Workspace | None = app.state.workspace
        if ws is None:
            return {"workspace_open": False, "runs": [], "publications": []}
        return {
            "workspace_open": True,
            "runs": list_runs(ws.root),
            "publications": list_publications(ws.root),
        }

    @app.post("/api/workspace/pick")
    async def pick() -> dict[str, Any]:
        if fixed_path is not None:
            return {"cancelled": False, "folder": inspect_folder(Path(fixed_path)).to_dict()}
        if not picker_lock.acquire(blocking=False):
            raise _error(409, "picker_busy", "A folder picker is already open.")
        try:
            last = recent.paths()
            start = Path(last[0]).parent if last else None
            chosen = await anyio.to_thread.run_sync(picker, PICK_PROMPT, start)
        except PickerUnavailable as exc:
            raise _error(501, "picker_unavailable", str(exc)) from None
        except PickerError as exc:
            raise _error(500, "picker_failed", str(exc)) from None
        finally:
            picker_lock.release()
        if chosen is None:
            return {"cancelled": True, "folder": None}
        info = inspect_folder(chosen)
        if info.state != MISSING:
            picked.add(info.path)
        return {"cancelled": False, "folder": info.to_dict()}

    @app.post("/api/workspace/inspect")
    def inspect(body: PathBody) -> dict[str, Any]:
        return {"folder": inspect_folder(approved(body.path)).to_dict()}

    @app.post("/api/workspace/init")
    def init(body: PathBody) -> dict[str, Any]:
        path = approved(body.path)
        storage = storage_ok(path)
        with open_lock:
            check_jobs_idle()
            try:
                workspace = init_workspace(path, __version__)
            except WorkspaceError as exc:
                raise workspace_error(exc) from None
            return activate(workspace, storage)

    @app.post("/api/workspace/open")
    def open_(body: OpenBody) -> dict[str, Any]:
        path = approved(body.path)
        storage = storage_ok(path)
        with open_lock:
            check_jobs_idle()
            try:
                workspace = open_workspace(path, allow_migrate=body.migrate)
            except WorkspaceError as exc:
                raise workspace_error(exc) from None
            return activate(workspace, storage)
