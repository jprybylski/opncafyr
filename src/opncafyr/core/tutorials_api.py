"""Routes for tutorials (#69, ADR 0010).

- ``GET /api/tutorials``: shipped and study tutorials with their problems, and progress.
- ``GET /api/tutorials/file?key=``: one tutorial's steps, hash and problems.
- ``POST /api/tutorials/copy {key}``: copy an example into the study's ``tutorials/``.
- ``PUT /api/tutorials/progress {key, step, paused, active, finished}``.
- ``DELETE /api/tutorials/progress?key=``: reset one tutorial's progress (all without key).

The only state these write is the user's own progress file (and a copied example). No route
here starts a job, changes a workspace record or calls a workflow.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from opncafyr.core.tutorials import KEY_RE, TutorialError, Tutorials

STATUS = {"not_found": 404, "no_workspace": 409, "invalid": 422, "exists": 409}
KEY = KEY_RE.pattern


class KeyBody(BaseModel):
    key: str = Field(pattern=KEY)


class ProgressBody(BaseModel):
    key: str = Field(pattern=KEY)
    step: str = Field(pattern=r"^[a-z][a-z0-9-]{0,63}$")
    paused: bool = False
    active: bool = True
    finished: bool = False


def mount_tutorials_api(app: FastAPI, tutorials: Tutorials) -> None:
    app.state.tutorials = tutorials

    def root() -> Path | None:
        ws = app.state.workspace
        return ws.root if ws is not None else None

    def call(fn, *args, **kw) -> Any:
        try:
            return fn(*args, **kw)
        except TutorialError as exc:
            raise HTTPException(
                status_code=STATUS.get(exc.code, 400),
                detail={"code": exc.code, "message": exc.message},
            ) from None

    @app.get("/api/tutorials")
    def listing() -> dict[str, Any]:
        where = root()
        return {
            **tutorials.listing(where),
            "workspace_open": where is not None,
            "progress": tutorials.progress(),
        }

    @app.get("/api/tutorials/file")
    def one(key: str = Query(pattern=KEY)) -> dict[str, Any]:
        return call(tutorials.get, key, root())

    @app.post("/api/tutorials/copy")
    def copy(body: KeyBody) -> dict[str, Any]:
        where = root()
        if where is None:
            raise HTTPException(
                status_code=409,
                detail={"code": "no_workspace", "message": "Open a study folder first."},
            )
        return {"path": call(tutorials.copy, body.key, where)}

    @app.put("/api/tutorials/progress")
    def save(body: ProgressBody) -> dict[str, Any]:
        return call(
            tutorials.save,
            body.key,
            root(),
            body.step,
            paused=body.paused,
            active=body.active,
            finished=body.finished,
        )

    @app.delete("/api/tutorials/progress")
    def reset(key: str | None = Query(default=None, pattern=KEY)) -> dict[str, Any]:
        return tutorials.reset(key)
