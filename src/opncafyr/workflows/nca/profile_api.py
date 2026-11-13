"""Routes for study workflow profiles (#68) under ``/api/nca/profiles``.

- ``GET /api/nca/profiles``: the shipped examples and the study's profile files, each with
  its problems, plus the followed profile and its progress.
- ``POST /api/nca/profiles/copy {name}``: copy an example into ``profiles/`` (never
  overwrites).
- ``POST /api/nca/profiles/select {name}`` / ``POST /api/nca/profiles/clear``.
- ``PUT /api/nca/profiles/current/steps/{step_id} {done, items}``: tick a step.
- ``PUT /api/nca/profiles/current/slots/{slot} {run_id}``: the completed run for a slot.
- ``PUT /api/nca/profiles/current/publications/{step_id} {publication_id}``.
- ``POST /api/nca/profiles/current/adopt {sha256}``: follow the file's new text.
- ``GET /api/nca/profiles/candidates``: completed runs and recorded publications to choose
  from, with nothing chosen.

Every write is a choice the user makes on the Study profile page. None starts, changes or
publishes a run.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi import Path as PathParam
from pydantic import BaseModel, Field

from opncafyr.core.guidance import Catalog
from opncafyr.workflows.nca import study_profile as sp

NAME = r"^[a-z][a-z0-9-]{0,63}$"
FILE = r"^[a-z][a-z0-9-]{0,63}\.yaml$"
STEP_ID = PathParam(pattern=NAME)

STATUS = {
    "no_workspace": 409,
    "not_found": 404,
    "exists": 409,
    "profile_invalid": 422,
    "state_invalid": 409,
    "no_profile": 409,
    "profile_changed": 409,
    "profile_replaced": 409,
    "bad_step": 422,
    "run_not_completed": 409,
    "run_in_other_slot": 409,
    "slots_empty": 409,
    "runs_differ": 409,
}


def _failure(exc: sp.ProfileError) -> HTTPException:
    return HTTPException(
        status_code=STATUS.get(exc.code, 400),
        detail={"code": exc.code, "message": exc.message, **exc.extra},
    )


class FileBody(BaseModel):
    name: str = Field(pattern=FILE)


class StepBody(BaseModel):
    done: bool = False
    items: list[str] | None = Field(default=None, max_length=50)


class SlotBody(BaseModel):
    run_id: str | None = Field(default=None, pattern=r"^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{4}$")


class PublicationBody(BaseModel):
    publication_id: str | None = Field(
        default=None, pattern=r"^pub-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{4}$"
    )


class AdoptBody(BaseModel):
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


def mount(app: FastAPI, ctx: Any) -> None:
    def catalog() -> Catalog:
        return app.state.guidance

    def root():
        return ctx.workspace().root

    def call(fn, *args) -> Any:
        try:
            return fn(*args)
        except sp.ProfileError as exc:
            raise _failure(exc) from None

    @app.get("/api/nca/profiles")
    def profiles() -> dict[str, Any]:
        ws = app.state.workspace
        where = ws.root if ws is not None else None
        out = call(sp.listing, where, catalog())
        out["workspace_open"] = ws is not None
        if where is None:
            out["current"] = None
        else:
            try:
                out["current"] = sp.status(where, catalog())
            except sp.ProfileError as exc:  # show the list; say what is wrong with progress
                out["current"] = {
                    "selected": None,
                    "error": {"code": exc.code, "message": exc.message},
                }
        return out

    @app.post("/api/nca/profiles/copy")
    def copy(body: FileBody) -> dict[str, Any]:
        return {"path": call(sp.copy_example, root(), body.name)}

    @app.post("/api/nca/profiles/select")
    def select(body: FileBody) -> dict[str, Any]:
        return call(sp.select, root(), body.name, catalog())

    @app.post("/api/nca/profiles/clear")
    def clear() -> dict[str, Any]:
        call(sp.clear, root())
        return {"selected": None}

    @app.put("/api/nca/profiles/current/steps/{step_id}")
    def mark(body: StepBody, step_id: str = STEP_ID) -> dict[str, Any]:
        if body.items is not None and any(not sp.valid_id(i) for i in body.items):
            raise _failure(sp.ProfileError("bad_step", "That checklist has no such item."))
        return call(sp.mark_step, root(), catalog(), step_id, body.done, body.items)

    @app.put("/api/nca/profiles/current/slots/{slot}")
    def slot(body: SlotBody, slot: str = PathParam(pattern=NAME)) -> dict[str, Any]:
        return call(sp.assign_run, root(), catalog(), slot, body.run_id)

    @app.put("/api/nca/profiles/current/publications/{step_id}")
    def publication(body: PublicationBody, step_id: str = STEP_ID) -> dict[str, Any]:
        return call(sp.assign_publication, root(), catalog(), step_id, body.publication_id)

    @app.post("/api/nca/profiles/current/adopt")
    def adopt(body: AdoptBody) -> dict[str, Any]:
        return call(sp.adopt, root(), catalog(), body.sha256)

    @app.get("/api/nca/profiles/candidates")
    def candidates() -> dict[str, Any]:
        return call(sp.candidates, root(), catalog())

    def context_for(
        step_id: str | None, kind: str, run_ids: set[str] | None = None
    ) -> dict[str, Any] | None:
        """The workflow_context for a run or publication the user makes for a step."""
        if step_id is None:
            return None
        return call(sp.context_for, root(), catalog(), step_id, kind, run_ids)

    ctx.profile_context = context_for
