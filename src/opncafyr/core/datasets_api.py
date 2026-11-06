"""Datasets API (#21): scan the open workspace, preview one file, import it.

- ``GET /api/datasets`` lists importable files in the workspace root and
  ``source-data/`` (``core.datasets``), without data rows.
- ``POST /api/datasets/preview`` returns columns, types, row count, the first rows and
  the workflow annotations of one listed file.
- ``POST /api/datasets/import`` re-hashes the file and records its path, size and
  SHA-256 in ``workspace.json`` ``sources``. Only ``.opncafyr/`` is written.

Paths travel in JSON bodies, never in URLs. POSTs need the session secret like every
state-changing request (``core.security``). Responses carry data values to the page on
this origin only; nothing here logs them.
"""

from __future__ import annotations

import threading
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from opncafyr.core.datasets import (
    DEFAULT_HEAD_ROWS,
    MAX_HEAD_ROWS,
    SCAN_FOLDERS,
    DatasetError,
    DatasetService,
    fingerprint,
)
from opncafyr.core.workspace import Workspace, WorkspaceError, add_source

STATUS = {
    "invalid_path": 400,
    "not_allowed": 403,
    "not_found": 404,
    "no_workspace": 409,
    "unsupported": 415,
    "unreadable": 422,
    "permission": 403,
}


class PathBody(BaseModel):
    path: str = Field(min_length=1, max_length=1024)


class PreviewBody(PathBody):
    rows: int = Field(default=DEFAULT_HEAD_ROWS, ge=0, le=MAX_HEAD_ROWS)


def _error(code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=STATUS.get(code, 400), detail={"code": code, "message": message}
    )


def mount_datasets_api(app: FastAPI, service: DatasetService) -> None:
    import_lock = threading.Lock()

    def workspace() -> Workspace:
        ws: Workspace | None = app.state.workspace
        if ws is None:
            raise _error("no_workspace", "Open a study folder first.")
        return ws

    @app.get("/api/datasets")
    def scan() -> dict[str, Any]:
        ws: Workspace | None = app.state.workspace
        base: dict[str, Any] = {"folders": list(SCAN_FOLDERS), "formats": service.formats}
        if ws is None:
            return {"workspace_open": False, "files": [], **base}
        imported = {s["path"]: s for s in ws.document.get("sources", [])}
        files = service.scan(ws.root)
        for f in files:
            source = imported.get(f["path"])
            f["imported"] = source is not None and source["sha256"] == f.get("sha256")
        return {"workspace_open": True, "files": files, **base}

    @app.post("/api/datasets/preview")
    def preview(body: PreviewBody) -> dict[str, Any]:
        ws = workspace()
        try:
            return {"dataset": service.preview(ws.root, body.path, body.rows)}
        except DatasetError as exc:
            raise _error(exc.code, exc.message) from None

    @app.post("/api/datasets/import")
    def import_(body: PathBody) -> dict[str, Any]:
        with import_lock:
            ws = workspace()
            try:
                path, rel, _ = service.locate(ws.root, body.path)
                size, sha256 = fingerprint(path)
            except DatasetError as exc:
                raise _error(exc.code, exc.message) from None
            except OSError:
                raise _error("unreadable", "This file could not be read.") from None
            try:
                updated, entry, created = add_source(ws, rel, size, sha256)
            except WorkspaceError as exc:
                raise _error(exc.code, exc.message) from None
            if app.state.workspace is ws:
                app.state.workspace = updated
            return {"source": entry, "created": created}
