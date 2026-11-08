"""Version history for the open study folder (#56, ADR 0008).

Opens gitneighbr (``adapters/gitneighbr``) on the open workspace, in managed mode, with
the policy below. Git snapshots are an optional history of files; they are not runs,
publications or builds, which stay immutable, hash-checked records of their own.

Routes:
- ``GET /api/nca/history``: whether a workspace is open, whether the tool is available
  (and why not), and whether its page is running.
- ``POST /api/nca/history {remote}``: start (or restart) it for the open workspace.
  ``remote`` turns on Send/Update for this session only; it is off unless asked for.
- ``DELETE /api/nca/history``: stop it.

Nothing here runs Git, and nothing happens on workspace selection: no init, fetch or
connection until the user acts inside the page.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from opncafyr.core.migrations import BACKUP_DIR
from opncafyr.core.runs import RUNS_DIR
from opncafyr.core.workspace import WORKSPACE_META_DIR
from opncafyr.paths import state_dir
from opncafyr.workflows.nca.cleaning import CLEANED_DIR
from opncafyr.workflows.nca.format_check import CHECKS_DIR
from opncafyr.workflows.nca.publish.projects import BUILDS, OUTPUTS
from opncafyr.workflows.nca.publish.report import CHECKS_DIR as PUBLISH_CHECKS_DIR
from opncafyr.workflows.nca.recipe import RECIPES_DIR

if TYPE_CHECKING:
    from opncafyr.core.workspace import Workspace

TOOL = "gitneighbr"
ROUTE = f"/apps/{TOOL}/"
META = WORKSPACE_META_DIR
PUBLICATIONS_DIR = f"{META}/publications"
SOURCE_DATA = "source-data"


def policy() -> dict[str, Any]:
    """The gitneighbr launcher policy for an NCA study folder.

    - **Groups** say what each changed file is, in the folder's own terms. gitneighbr
      puts a file in the first group that matches, so the narrower groups come first:
      builds before the report and deck sources, recipes before the app's other files.
    - **Protected** paths are the app's immutable records (runs, publications, builds)
      and its own bookkeeping: no Git action may change them in the folder, so getting
      updates never replaces a completed run or a build.
    - **Review** labels mark files that can contain subject-level or identifiable data;
      sending them needs an extra confirmation. Nothing is classified as "safe".
    - **Ignore template** rules are only offered when the user sets up version history.
      They leave out noise and files not needed to reproduce results (scratch, caches the
      app rebuilds, render logs); whether to snapshot data, runs or outputs is the user's
      choice, per file (ADR 0008).
    """
    return {
        "schema_version": 1,
        "groups": [
            {
                "id": "records",
                "label": "Run and publication records",
                "description": (
                    "Completed runs and publications. The app keeps these fixed and "
                    "hash-checked; a snapshot is only a copy."
                ),
                "globs": [f"{RUNS_DIR}/**", f"{PUBLICATIONS_DIR}/**"],
            },
            {
                "id": "generated",
                "label": "Generated files",
                "description": (
                    "Published outputs, report and deck builds, and the copies of published "
                    "files the report and deck use."
                ),
                "globs": [
                    f"{OUTPUTS}/",
                    f"report/{BUILDS}/",
                    f"deck/{BUILDS}/",
                    f"report/{OUTPUTS}/",
                    f"deck/{OUTPUTS}/",
                    "deck/build/",
                ],
            },
            {
                "id": "sources",
                "label": "Editable sources",
                "description": "Cleaning recipes and the report and deck projects you edit.",
                "globs": [f"{RECIPES_DIR}/**", "report/**", "deck/**"],
            },
            {
                "id": "app",
                "label": "App working files",
                "description": (
                    f"The app's own settings, cleaned-data caches and check results in {META}."
                ),
                "globs": [f"{META}/**"],
            },
            {
                "id": "data",
                "label": "Source data",
                "description": "The study's data files.",
                "globs": [f"{SOURCE_DATA}/**"],
            },
        ],
        "default_group": {"id": "other", "label": "Other files"},
        "protected": [
            f"{RUNS_DIR}/**",
            f"{PUBLICATIONS_DIR}/**",
            f"report/{BUILDS}/**",
            f"deck/{BUILDS}/**",
            f"{META}/workspace.json",
            f"{META}/jobs/**",
            f"{META}/backups/**",
        ],
        "review": [
            {
                "id": "study-data",
                "label": "May identify subjects",
                "message": (
                    "Source data, cleaned data, run results, outputs, reports and decks can "
                    "contain subject-level or identifiable information. Send them only where "
                    "you are allowed to."
                ),
                "globs": [
                    f"{SOURCE_DATA}/**",
                    f"{META}/cleaned/**",
                    f"{META}/checks/**",
                    f"{RUNS_DIR}/**",
                    f"{OUTPUTS}/",
                    f"report/{BUILDS}/",
                    f"deck/{BUILDS}/",
                    f"report/{OUTPUTS}/",
                    f"deck/{OUTPUTS}/",
                ],
            }
        ],
        "send_blocked": [],
        "ignore_template": [
            {"rule": f"{META}/jobs/", "reason": "Job logs and scratch space for each job."},
            {
                "rule": f"{CLEANED_DIR}/",
                "reason": (
                    "Cleaned-data caches the app rebuilds from the source data and recipe. "
                    "Each run keeps its own copy of its input."
                ),
            },
            {"rule": f"{CHECKS_DIR}/", "reason": "Saved data-check results, rebuilt when needed."},
            {
                "rule": f"{PUBLISH_CHECKS_DIR}/",
                "reason": "Saved report and deck check results, rebuilt when needed.",
            },
            {
                "rule": f"{META}/{BACKUP_DIR}/",
                "reason": "Copies of the folder's settings kept before an app upgrade.",
            },
            {"rule": "report/.quarto/", "reason": "Quarto's render cache."},
            {"rule": "report/.rpfy-logs/", "reason": "Render logs from reportifyr."},
            {
                "rule": "deck/build/",
                "reason": "The deck editor's own previews. Finished decks are in deck/builds/.",
            },
            {"rule": ".DS_Store", "reason": "macOS folder settings."},
            {"rule": "Thumbs.db", "reason": "Windows thumbnail cache."},
            {"rule": "~$*", "reason": "Lock files Word and PowerPoint make while a file is open."},
            {"rule": ".Rhistory", "reason": "R's command history."},
        ],
    }


def git_config_path() -> Any:
    """The user's Git name and email for version history: app state, never a study."""
    return state_dir() / "gitneighbr" / "gitconfig"


class OpenBody(BaseModel):
    remote: bool = False


def mount(app: FastAPI) -> None:
    def workspace() -> Workspace:
        ws = app.state.workspace
        if ws is None:
            raise HTTPException(
                409, {"code": "no_workspace", "message": "Open a study folder first."}
            )
        return ws

    def running(sup: Any) -> bool:
        from opncafyr.core.supervisor import ChildState

        return TOOL in sup.apps and sup.state(TOOL) in (ChildState.RUNNING, ChildState.STARTING)

    @app.get("/api/nca/history")
    def status() -> dict[str, Any]:
        sup = app.state.supervisor
        installed = TOOL in app.state.tools and TOOL in sup.apps
        health = app.state.health.status(TOOL) if installed else None
        return {
            "workspace_open": app.state.workspace is not None,
            "installed": installed,
            "available": health is not None and health.state == "ok",
            "detail": None if health is None else health.detail,
            "running": installed and running(sup),
            "remote": bool(sup.session(TOOL).get("remote")) if installed else False,
            "route": ROUTE,
        }

    @app.post("/api/nca/history")
    def open_history(body: OpenBody) -> dict[str, Any]:
        from opncafyr.core.supervisor import LaunchError

        ws = workspace()
        sup = app.state.supervisor
        if TOOL not in sup.apps:
            raise HTTPException(
                503, {"code": "unavailable", "message": "Version history is not installed."}
            )
        health = app.state.health.status(TOOL)
        if health.state != "ok":
            raise HTTPException(
                503,
                {
                    "code": "unavailable",
                    "message": f"Version history is unavailable: {health.detail or 'not ready'}",
                },
            )
        try:
            sup.start(
                TOOL,
                {
                    "workspace_root": str(ws.root),
                    "git_config": str(git_config_path()),
                    "remote": body.remote,
                    "policy": policy(),
                },
            )
        except (LaunchError, ValueError, FileNotFoundError) as exc:
            raise HTTPException(
                503, {"code": "failed", "message": f"Version history didn't start: {exc}"}
            ) from None
        return {"route": ROUTE, "remote": body.remote}

    @app.delete("/api/nca/history")
    def close_history() -> dict[str, Any]:
        sup = app.state.supervisor
        if TOOL in sup.apps:
            sup.stop(TOOL)
        return {"running": False}
