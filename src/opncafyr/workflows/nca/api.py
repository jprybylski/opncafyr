"""The NCA pack's API, mounted by ``core.app`` through ``WorkflowPack.mount``.

Every route is under ``/api/nca/`` and passes the core security middleware (Host check;
the session secret on every POST/PUT). Data values go to the page on this origin only and
are never logged.

Recipes (#22):
- ``GET  /api/nca/sources``: imported sources (id, path) to build a recipe on.
- ``GET  /api/nca/recipes``; ``POST /api/nca/recipes {source_id}`` makes an empty one.
- ``GET  /api/nca/recipes/{id}``: the recipe, its hash and the columns after its steps.
- ``PUT  /api/nca/recipes/{id} {recipe}``: validate (schema and rules) and save.
- ``POST /api/nca/recipes/{id}/preview`` / ``.../apply``: start the cleaning job, or
  return a still-valid earlier result.
- ``GET  /api/nca/recipes/{id}/result?kind=preview|full``: counts, first rows, rejected.
"""

from __future__ import annotations

import re
import secrets
import threading
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException
from fastapi import Path as PathParam
from pydantic import BaseModel, Field

from opncafyr.core.jobmanager import JobManagerError
from opncafyr.core.manifest import atomic_write_text
from opncafyr.core.workspace import Workspace, resolve_in_root
from opncafyr.workflows.nca import recipe as recipes
from opncafyr.workflows.nca.cleaning import (
    PREVIEW_ROWS,
    CleaningError,
    CleaningService,
    check_output,
    load_result,
    plan,
)

RECIPE_ID = PathParam(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")
MAX_RECIPE_BYTES = 1 << 20

STATUS = {
    "no_workspace": 409,
    "not_found": 404,
    "source_not_found": 404,
    "recipe_invalid": 422,
    "source_changed": 409,
    "not_ready": 404,
    "jobs_unavailable": 409,
}


def error(code: str, message: str, **extra: Any) -> HTTPException:
    return HTTPException(
        status_code=STATUS.get(code, 400), detail={"code": code, "message": message, **extra}
    )


class NewRecipe(BaseModel):
    source_id: str = Field(min_length=1, max_length=200)


class SaveRecipe(BaseModel):
    recipe: dict[str, Any]


class Context:
    """What the routes share: the app (for the open workspace and job manager)."""

    def __init__(self, app: FastAPI) -> None:
        self.app = app
        self.lock = threading.Lock()
        self.cleaning = CleaningService()

    def workspace(self) -> Workspace:
        ws: Workspace | None = self.app.state.workspace
        if ws is None:
            raise error("no_workspace", "Open a study folder first.")
        return ws

    def recipe_path(self, root: Path, recipe_id: str) -> Path:
        return resolve_in_root(root, f"{recipes.RECIPES_DIR}/{recipe_id}.yaml")

    def load_recipe(self, recipe_id: str) -> dict[str, Any]:
        ws = self.workspace()
        path = self.recipe_path(ws.root, recipe_id)
        if not path.is_file():
            raise error("not_found", "There is no such recipe.")
        if path.stat().st_size > MAX_RECIPE_BYTES:
            raise error("recipe_invalid", "The recipe file is too large.")
        try:
            doc = recipes.loads(path.read_text("utf-8"))
        except recipes.RecipeError as exc:
            raise error("recipe_invalid", str(exc), step=exc.step) from None
        if doc["id"] != recipe_id:
            raise error("recipe_invalid", "The recipe's id does not match its file name.")
        return doc

    def plan(self, doc: dict[str, Any], sample_rows: int = 0):
        ws = self.workspace()
        try:
            return plan(ws.root, ws.document, doc, sample_rows)
        except recipes.RecipeError as exc:
            raise error("recipe_invalid", str(exc), step=exc.step) from None
        except CleaningError as exc:
            raise error(exc.code, exc.message) from None

    def cleaned_folder(self, doc: dict[str, Any], sample_rows: int = 0) -> Path | None:
        """The folder of a valid result for ``doc`` as it is now, if there is one."""
        target = self.plan(doc, sample_rows)
        folder = resolve_in_root(self.workspace().root, target.output)
        if folder.is_dir() and check_output(folder, target) is None:
            return folder
        return None


def _summary(doc: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": doc["id"],
        "source_id": doc["source_id"],
        "steps": len(doc["steps"]),
        "sha256": recipes.recipe_hash(doc),
    }


def mount(app: FastAPI) -> Context:
    ctx = Context(app)
    app.state.nca = ctx

    @app.get("/api/nca/sources")
    def sources() -> dict[str, Any]:
        ws: Workspace | None = app.state.workspace
        if ws is None:
            return {"workspace_open": False, "sources": []}
        return {
            "workspace_open": True,
            "sources": [{"id": s["id"], "path": s["path"]} for s in ws.document["sources"]],
        }

    @app.get("/api/nca/recipes")
    def list_recipes() -> dict[str, Any]:
        ws: Workspace | None = app.state.workspace
        if ws is None:
            return {"workspace_open": False, "recipes": []}
        folder = ws.root / recipes.RECIPES_DIR
        out = []
        for path in sorted(folder.glob("*.yaml")) if folder.is_dir() else []:
            try:
                out.append(_summary(ctx.load_recipe(path.stem)))
            except HTTPException as exc:
                out.append({"id": path.stem, "error": exc.detail["message"]})
        return {"workspace_open": True, "recipes": out}

    @app.post("/api/nca/recipes")
    def create_recipe(body: NewRecipe) -> dict[str, Any]:
        with ctx.lock:
            ws = ctx.workspace()
            if not any(s["id"] == body.source_id for s in ws.document["sources"]):
                raise error("source_not_found", "Import the file before making a recipe.")
            stem = re.sub(r"[^a-z0-9]+", "-", body.source_id.lower()).strip("-")[:50] or "recipe"
            while True:
                recipe_id = f"{stem}-{secrets.token_hex(2)}"
                path = ctx.recipe_path(ws.root, recipe_id)
                if not path.exists():
                    break
            doc = recipes.new_recipe(recipe_id, body.source_id)
            atomic_write_text(path, recipes.dumps(doc))
        return {"recipe": doc, **_summary(doc)}

    @app.get("/api/nca/recipes/{recipe_id}")
    def get_recipe(recipe_id: str = RECIPE_ID) -> dict[str, Any]:
        doc = ctx.load_recipe(recipe_id)
        out: dict[str, Any] = {"recipe": doc, **_summary(doc)}
        try:
            target = ctx.plan(doc)
            out["header"] = target.header
            out["columns"] = target.columns
            out["problem"] = None
        except HTTPException as exc:
            out["problem"] = exc.detail
        return out

    @app.put("/api/nca/recipes/{recipe_id}")
    def save_recipe(body: SaveRecipe, recipe_id: str = RECIPE_ID) -> dict[str, Any]:
        doc = body.recipe
        if doc.get("id") != recipe_id:
            raise error("recipe_invalid", "The recipe's id does not match the URL.")
        with ctx.lock:
            ws = ctx.workspace()
            path = ctx.recipe_path(ws.root, recipe_id)
            if not path.is_file():
                raise error("not_found", "There is no such recipe.")
            old = ctx.load_recipe(recipe_id)
            if doc.get("source_id") != old["source_id"]:
                raise error("recipe_invalid", "A recipe stays with its source.")
            target = ctx.plan(doc)
            atomic_write_text(path, recipes.dumps(doc))
        return {"recipe": doc, **_summary(doc), "columns": target.columns}

    def start(recipe_id: str, sample_rows: int) -> dict[str, Any]:
        doc = ctx.load_recipe(recipe_id)
        ws = ctx.workspace()
        try:
            return ctx.cleaning.start(
                app.state.jobs, ws.root, ws.document, doc, sample_rows=sample_rows
            )
        except recipes.RecipeError as exc:
            raise error("recipe_invalid", str(exc), step=exc.step) from None
        except CleaningError as exc:
            raise error(exc.code, exc.message) from None
        except JobManagerError as exc:
            raise error("jobs_unavailable", str(exc)) from None

    @app.post("/api/nca/recipes/{recipe_id}/preview")
    def preview(recipe_id: str = RECIPE_ID) -> dict[str, Any]:
        return start(recipe_id, PREVIEW_ROWS)

    @app.post("/api/nca/recipes/{recipe_id}/apply")
    def apply(recipe_id: str = RECIPE_ID) -> dict[str, Any]:
        return start(recipe_id, 0)

    @app.get("/api/nca/recipes/{recipe_id}/result")
    def result(
        recipe_id: str = RECIPE_ID, kind: Literal["preview", "full"] = "full"
    ) -> dict[str, Any]:
        doc = ctx.load_recipe(recipe_id)
        folder = ctx.cleaned_folder(doc, PREVIEW_ROWS if kind == "preview" else 0)
        if folder is None:
            raise error("not_ready", "This recipe has not been run as it is now.")
        return {"kind": kind, **load_result(folder)}

    return ctx
