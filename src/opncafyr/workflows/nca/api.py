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

Mapping (#23), on the full cleaned data:
- ``GET  /api/nca/recipes/{id}/mapping-help``: suggestions and ``needs_choice`` questions
  for every role, each column's distinct values (for flag tables and units), the route
  values with opnca-r's reading of each, and what still blocks the saved mapping.
- ``PUT  /api/nca/recipes/{id}/mapping {mapping}``: check and save the mapping block.

Checks (#24), for one analyte (and specimen) of the full cleaned data:
- ``GET  /api/nca/recipes/{id}/analytes``: the exact analyte values, with rows and specimens.
- ``POST /api/nca/recipes/{id}/checks {analyte_value, specimen_value}``: every finding,
  with a ``key`` on each warning for its acknowledgement; also starts opnca-r's own data
  check, whose issues come back from ``POST .../format-check`` as info.
- ``POST /api/nca/runs``: refuses with 409 and the findings while anything blocks or a
  warning is not acknowledged.

Runs (#25):
- ``POST /api/nca/runs {recipe_id, analyte_value, specimen_value, analyte_label,
  acknowledged, blq_rules, blq_method}``: checks, gates, and writes the run's frozen
  one-analyte snapshot (``snapshot.py``). 201 with the manifest.
- ``GET  /api/nca/runs``: every run with its analyte and state; ``GET /api/nca/runs/{id}``.
- ``POST /api/nca/runs/{id}/rerun``: a new run with the same choices (#26); a run itself
  never changes. The integrity check is core's ``GET /api/runs/{id}/verify``.

Sessions (#27):
- ``POST /api/nca/runs/{id}/session``: start opnca-r on the run (replacing any other
  session) and wait until it is ready under ``/apps/opnca/``.
- ``DELETE /api/nca/runs/{id}/session``; ``GET /api/nca/session``: which run is open.

Save and completion (#28):
- ``POST /api/nca/runs/{id}/save-status``: looks for opnca-r's save in the run folder
  (the page polls it while a run is open). On ``COMPLETE`` it stops the session and
  checks the save; the run moves to ``reviewing`` or ``failed``.
- ``POST /api/nca/runs/{id}/complete``: the user confirms a reviewed run; it is sealed.

Replay (#29):
- A verified save starts the replay job (``replay.py``): ``analysis.R`` re-run in a
  copy and compared with ``results/``. ``POST /api/nca/runs/{id}/replay {rtol}`` runs it
  again. ``save-status`` on a run in review records the newest finished replay in the
  manifest and returns its differences; ``complete`` needs a passed replay.
"""

from __future__ import annotations

import re
import secrets
import threading
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException
from fastapi import Path as PathParam
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from opncafyr.core import runs as runs_core
from opncafyr.core.jobmanager import JobManagerError
from opncafyr.core.manifest import atomic_write_text
from opncafyr.core.redaction import REDACTOR
from opncafyr.core.workspace import Workspace, resolve_in_root
from opncafyr.workflows.nca import checks as checks_mod
from opncafyr.workflows.nca import mapping as mappings
from opncafyr.workflows.nca import recipe as recipes
from opncafyr.workflows.nca import replay as replay_mod
from opncafyr.workflows.nca import save as save_mod
from opncafyr.workflows.nca.cleaning import (
    PREVIEW_ROWS,
    CleaningError,
    CleaningService,
    check_output,
    load_result,
    plan,
    read_cleaned,
)
from opncafyr.workflows.nca.format_check import FormatChecks
from opncafyr.workflows.nca.preset import PresetError
from opncafyr.workflows.nca.roles import (
    BY_ID,
    ROLES,
    columns_from_rows,
    distinct_values,
    suggest,
)
from opncafyr.workflows.nca.routes import route_values
from opncafyr.workflows.nca.session import SessionError, Sessions
from opncafyr.workflows.nca.snapshot import SnapshotError, create_run
from opncafyr.workflows.nca.units import UNIT_ROLES, header_hint

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
    "checks_blocking": 409,
    "warnings_unacknowledged": 409,
    "needs_choice": 409,
    "not_implemented": 501,
    "column_clash": 409,
    "column_names": 409,
    "occasion_unsupported": 409,
    "ids_collide": 409,
    "preset_invalid": 409,
    "run_invalid": 409,
    "run_sealed": 409,
    "bad_state": 409,
    "run_not_open": 409,
    "run_saved": 409,
    "input_changed": 409,
    "session_failed": 503,
    "replay_required": 409,
}


def error(code: str, message: str, **extra: Any) -> HTTPException:
    return HTTPException(
        status_code=STATUS.get(code, 400), detail={"code": code, "message": message, **extra}
    )


class NewRecipe(BaseModel):
    source_id: str = Field(min_length=1, max_length=200)


class SaveRecipe(BaseModel):
    recipe: dict[str, Any]


class SaveMapping(BaseModel):
    mapping: dict[str, Any]


class SelectionBody(BaseModel):
    analyte_value: str = Field(max_length=1000)
    specimen_value: str | None = Field(default=None, max_length=1000)

    def selection(self) -> checks_mod.Selection:
        return checks_mod.Selection(self.analyte_value, self.specimen_value)


class ReplayBody(BaseModel):
    rtol: float = Field(default=1e-10, gt=0, le=1e-3)


class StartRun(SelectionBody):
    recipe_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")
    analyte_label: str | None = Field(default=None, max_length=200)
    acknowledged: list[str] = Field(default_factory=list, max_length=1000)
    blq_rules: dict[str, str] | None = None
    blq_method: str | None = None


class Context:
    """What the routes share: the app (for the open workspace and job manager)."""

    def __init__(self, app: FastAPI) -> None:
        self.app = app
        self.lock = threading.Lock()
        self.cleaning = CleaningService()
        self.format_checks = FormatChecks()
        self.sessions = Sessions()
        self.replays: dict[str, str] = {}

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

    def require_cleaned(self, doc: dict[str, Any]) -> Path:
        folder = self.cleaned_folder(doc)
        if folder is None:
            raise error("not_ready", "Apply the recipe to all rows first.")
        return folder

    def save(self, doc: dict[str, Any]) -> Any:
        """Check ``doc`` against its source and write it. Callers hold ``self.lock``."""
        ws = self.workspace()
        path = self.recipe_path(ws.root, doc["id"])
        if not path.is_file():
            raise error("not_found", "There is no such recipe.")
        old = self.load_recipe(doc["id"])
        if doc.get("source_id") != old["source_id"]:
            raise error("recipe_invalid", "A recipe stays with its source.")
        target = self.plan(doc)
        atomic_write_text(path, recipes.dumps(doc))
        return target

    def checked(self, recipe_id: str, selection: checks_mod.Selection) -> dict[str, Any]:
        """Every finding for one selection, keyed for acknowledgement."""
        doc = self.load_recipe(recipe_id)
        folder = self.require_cleaned(doc)
        target = self.plan(doc)
        header, rows = read_cleaned(folder)
        steps = load_result(folder, head_rows=0)["summary"]
        lineage = recipes.check(doc, target.header).lineage
        mapping = doc.get("mapping") or {}
        ids = [c for r in ("SUBJECT_ID", "SECONDARY_ID") if (c := mappings.role(mapping, r))]
        if ids:
            # Subject IDs are data: keep them out of every log line from here on.
            idx = [header.index(c) for c in ids if c in header]
            REDACTOR.register_values(row[i] for row in rows for i in idx if i < len(row))
        result = checks_mod.run_checks(
            header, rows, doc.get("mapping"), selection, lineage=lineage, steps=steps
        )
        digest = recipes.recipe_hash(doc)
        for finding in result["findings"]:
            if finding["level"] == checks_mod.WARNING:
                finding["key"] = checks_mod.finding_key(finding, digest)
        result.update(
            recipe=doc,
            recipe_sha256=digest,
            cleaned=folder,
            header=header,
            rows=rows,
            steps=steps,
        )
        return result

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
            target = ctx.save(doc)
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

    @app.get("/api/nca/recipes/{recipe_id}/mapping-help")
    def mapping_help(recipe_id: str = RECIPE_ID) -> dict[str, Any]:
        doc = ctx.load_recipe(recipe_id)
        header, rows = read_cleaned(ctx.require_cleaned(doc))
        columns = [c for c in columns_from_rows(header, rows)]
        mapping = doc.get("mapping") or mappings.empty_mapping()
        by_name = {c.name: c for c in columns}
        route_col = mappings.role(mapping, "ROUTE")
        hints = {}
        for name, kind in UNIT_ROLES.items():
            column = mappings.role(mapping, name)
            hint = header_hint(column, kind) if column else None
            if hint:
                hints[name] = hint
        return {
            "roles": [{"id": r.id, "label": r.label, "required": r.required} for r in ROLES],
            **suggest(columns),
            "mapping": mapping,
            "problems": mappings.problems(mapping),
            "columns": [distinct_values(c) for c in columns if not c.name.startswith("opncafyr_")],
            "routes": route_values(by_name[route_col].values) if route_col in by_name else [],
            "unit_hints": hints,
        }

    @app.put("/api/nca/recipes/{recipe_id}/mapping")
    def save_mapping(body: SaveMapping, recipe_id: str = RECIPE_ID) -> dict[str, Any]:
        with ctx.lock:
            doc = ctx.load_recipe(recipe_id)
            doc["mapping"] = body.mapping
            ctx.save(doc)
        return {
            "mapping": doc["mapping"],
            "sha256": recipes.recipe_hash(doc),
            "problems": mappings.problems(doc["mapping"]),
            "labels": {r: BY_ID[r].label for r in BY_ID},
        }

    @app.get("/api/nca/recipes/{recipe_id}/analytes")
    def analytes(recipe_id: str = RECIPE_ID) -> dict[str, Any]:
        doc = ctx.load_recipe(recipe_id)
        mapping = doc.get("mapping") or {}
        column = mappings.role(mapping, "ANALYTE")
        if not column:
            raise error("needs_choice", "Map the analyte column first.")
        header, rows = read_cleaned(ctx.require_cleaned(doc))
        a = header.index(column)
        specimen = mappings.role(mapping, "SPECIMEN")
        s = header.index(specimen) if specimen in header else None
        label = mappings.role(mapping, "ANALYTE_LABEL")
        lab = header.index(label) if label in header else None
        values: dict[str, dict[str, Any]] = {}
        for r in rows:
            entry = values.setdefault(
                r[a], {"value": r[a], "rows": 0, "specimens": {}, "labels": {}}
            )
            entry["rows"] += 1
            if s is not None:
                entry["specimens"][r[s]] = entry["specimens"].get(r[s], 0) + 1
            if lab is not None:
                entry["labels"][r[lab]] = entry["labels"].get(r[lab], 0) + 1
        return {
            "column": column,
            "specimen_column": specimen,
            "values": sorted(values.values(), key=lambda v: v["value"]),
        }

    def public(result: dict[str, Any]) -> dict[str, Any]:
        return {k: result[k] for k in ("findings", "summary", "accounting", "recipe_sha256")}

    @app.post("/api/nca/recipes/{recipe_id}/checks")
    def run_checks(body: SelectionBody, recipe_id: str = RECIPE_ID) -> dict[str, Any]:
        result = ctx.checked(recipe_id, body.selection())
        out = public(result)
        out["format"] = format_check(result, body)
        if out["format"]["findings"]:
            out["findings"] = out["findings"] + out["format"]["findings"]
        return out

    def format_check(result: dict[str, Any], body: SelectionBody) -> dict[str, Any]:
        mapping = result["recipe"].get("mapping") or {}
        if not mappings.role(mapping, "ANALYTE"):
            return {"job_id": None, "findings": []}
        try:
            return ctx.format_checks.start(
                app.state.jobs,
                ctx.workspace().root,
                result["cleaned"],
                mapping,
                body.selection(),
            )
        except JobManagerError as exc:
            raise error("jobs_unavailable", str(exc)) from None

    @app.post("/api/nca/recipes/{recipe_id}/format-check")
    def format_check_result(body: SelectionBody, recipe_id: str = RECIPE_ID) -> dict[str, Any]:
        return format_check(ctx.checked(recipe_id, body.selection()), body)

    @app.post("/api/nca/runs")
    def start_run_route(body: StartRun) -> Any:
        return start_run(body)

    def start_run(body: StartRun, parent_run_id: str | None = None) -> Any:
        result = ctx.checked(body.recipe_id, body.selection())
        verdict, concerned = checks_mod.gate(
            result["findings"], body.acknowledged, result["recipe_sha256"]
        )
        if verdict != "ok":
            message = (
                "Fix the blocking findings first."
                if verdict == "checks_blocking"
                else "Acknowledge every warning first."
            )
            raise error(verdict, message, findings=concerned)
        ws = ctx.workspace()
        try:
            with ctx.lock:
                manifest = create_run(
                    ws.root,
                    ws.document,
                    result,
                    body.selection(),
                    label=body.analyte_label,
                    acknowledged=body.acknowledged,
                    blq=body.blq_rules,
                    blq_method=body.blq_method,
                    parent_run_id=parent_run_id,
                )
        except SnapshotError as exc:
            raise error(exc.code, exc.message, **exc.extra) from None
        except PresetError as exc:
            raise error("preset_invalid", str(exc)) from None
        return JSONResponse(status_code=201, content={"run": manifest})

    def run_folder(run_id: str) -> Path:
        try:
            return runs_core.run_dir(ctx.workspace().root, run_id)
        except runs_core.RunError as exc:
            raise error("not_found", exc.message) from None

    def read_run(folder: Path) -> dict[str, Any]:
        try:
            return runs_core.read_manifest(folder)
        except runs_core.RunError as exc:
            raise error("run_invalid", exc.message) from None

    ctx.run_folder = run_folder
    ctx.read_run = read_run

    @app.get("/api/nca/runs")
    def list_runs() -> dict[str, Any]:
        ws: Workspace | None = app.state.workspace
        if ws is None:
            return {"workspace_open": False, "runs": []}
        from opncafyr.core.artifacts import list_runs as core_list

        return {"workspace_open": True, "runs": core_list(ws.root)}

    @app.get("/api/nca/runs/{run_id}")
    def get_run(run_id: str) -> dict[str, Any]:
        return {"run": read_run(run_folder(run_id))}

    @app.post("/api/nca/runs/{run_id}/session")
    def open_session(run_id: str) -> dict[str, Any]:
        folder = run_folder(run_id)
        try:
            return {"session": ctx.sessions.start(app.state.supervisor, folder)}
        except SessionError as exc:
            raise error(exc.code, exc.message) from None
        except PresetError as exc:
            raise error("preset_invalid", str(exc)) from None
        except runs_core.RunError as exc:
            raise error(exc.code, exc.message) from None

    @app.delete("/api/nca/runs/{run_id}/session")
    def close_session(run_id: str) -> dict[str, Any]:
        run_folder(run_id)
        return {"session": ctx.sessions.stop(app.state.supervisor, run_id)}

    @app.get("/api/nca/session")
    def session_state() -> dict[str, Any]:
        return {"session": ctx.sessions.current(app.state.supervisor)}

    @app.post("/api/nca/runs/{run_id}/save-status")
    def save_status(run_id: str) -> dict[str, Any]:
        folder = run_folder(run_id)
        with ctx.lock:
            manifest = read_run(folder)
            if manifest["state"] == "reviewing":
                return {
                    "save": {"state": "complete", "problems": []},
                    "run": replay_mod.sync(folder),
                    "replay": replay_state(folder),
                }
            if manifest["state"] not in ("preparing", "running"):
                return {"save": {"state": manifest["state"], "problems": []}, "run": manifest}
            sup = app.state.supervisor
            alive = ctx.sessions.current(sup)["run_id"] == run_id
            status = save_mod.check(folder, session_alive=alive)
            if status.state in ("complete", "failed") and alive:
                # Nothing more may be written: stop the session, then look again.
                ctx.sessions.stop(sup, run_id)
                status = save_mod.check(folder, session_alive=False)
            if status.state in ("complete", "failed"):
                manifest = save_mod.accept(folder, status)
                after_save(folder, manifest)
                manifest = read_run(folder)
        return {"save": status.to_dict(), "run": manifest}

    def after_save(folder: Path, manifest: dict[str, Any]) -> None:
        """A verified save is replayed at once (#29)."""
        if manifest["state"] == "reviewing":
            start_replay(folder, ReplayBody())

    def replay_timeout() -> float:
        tool = (app.state.tools or {}).get("opnca")
        contract = tool.jobs.get("replay_nca") if tool is not None else None
        if contract is not None and contract.timeout_s:
            return float(contract.timeout_s)
        return replay_mod.DEFAULT_TIMEOUT_S

    def start_replay(folder: Path, body: ReplayBody) -> str:
        try:
            job_id = replay_mod.start(
                app.state.jobs, folder, rtol=body.rtol, timeout_s=replay_timeout()
            )
        except runs_core.RunError as exc:
            raise error(exc.code, exc.message) from None
        except JobManagerError as exc:
            raise error("jobs_unavailable", str(exc)) from None
        ctx.replays[folder.name] = job_id
        return job_id

    def replay_state(folder: Path) -> dict[str, Any]:
        job_id = ctx.replays.get(folder.name)
        job = None
        if job_id:
            try:
                job = app.state.jobs.get(job_id)
            except KeyError:
                job = None
        found = replay_mod.latest(folder)
        report = found[1] if found else None
        return {
            "job": {"job_id": job_id, "state": job["state"], "message": job["message"]}
            if job
            else None,
            "status": report["status"] if report else None,
            "differences": report["differences"] if report else "",
        }

    @app.post("/api/nca/runs/{run_id}/replay")
    def replay_again(run_id: str, body: ReplayBody | None = None) -> dict[str, Any]:
        folder = run_folder(run_id)
        with ctx.lock:
            job_id = start_replay(folder, body or ReplayBody())
        return {"job_id": job_id}

    ctx.after_save = after_save

    @app.post("/api/nca/runs/{run_id}/complete")
    def complete(run_id: str) -> dict[str, Any]:
        folder = run_folder(run_id)
        with ctx.lock:
            manifest = read_run(folder)
            if manifest["state"] == "reviewing":
                manifest = replay_mod.sync(folder)
            if manifest["state"] != "reviewing":
                raise error(
                    "bad_state",
                    f"Only a run in review can be completed; this one is {manifest['state']}.",
                )
            replay = manifest.get("replay") or {}
            if replay.get("status") != "passed":
                raise error(
                    "replay_required",
                    "The saved analysis must replay and match its results first; only then "
                    "can the run be completed.",
                )
            try:
                sealed = runs_core.seal_run(folder)
            except runs_core.RunError as exc:
                raise error(exc.code, exc.message) from None
        return {"run": sealed}

    @app.post("/api/nca/runs/{run_id}/rerun")
    def rerun(run_id: str) -> Any:
        """A new run with this run's choices, on the recipe as it is now (#26).

        A run never changes once started; changing anything means a new run, linked to
        this one by ``lineage.parent_run_id``. Warnings acknowledged for the same recipe
        stay acknowledged; a changed recipe asks again.
        """
        import json

        folder = run_folder(run_id)
        settings = json.loads((folder / "settings.json").read_text("utf-8"))
        body = StartRun(
            recipe_id=settings["recipe"]["id"],
            analyte_value=settings["analyte"]["value"],
            specimen_value=(settings.get("specimen") or {}).get("value"),
            analyte_label=settings["analyte"]["label"],
            acknowledged=[a["key"] for a in settings["acknowledgements"]],
            blq_rules=settings["blq"]["rules"],
            blq_method=settings["blq"]["method"],
        )
        return start_run(body, parent_run_id=run_id)

    return ctx
