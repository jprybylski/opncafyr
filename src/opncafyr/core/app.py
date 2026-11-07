"""FastAPI application factory for the loopback controller.

One origin serves the home page, the API and the proxied child apps under
``/apps/<tool>/``. The lifespan owns the child supervisor and the job manager: children
and jobs are started by workflows and adapters, and all of them are stopped when the
controller exits. The lifespan also runs the lifecycle monitor (``core.lifecycle``): page
heartbeats, idle shutdown and the explicit Quit flow. The user picks the study workspace
through ``/api/workspace/*`` (``core.workspace_api``); opening one points the job manager
at it. ``/api/datasets/*`` (``core.datasets_api``) scans and previews its data files with
the importers of the installed workflow packs.
"""

from __future__ import annotations

import asyncio
import atexit
import contextlib
import logging
import time
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from html import escape
from importlib import resources
from pathlib import Path
from typing import Any, Literal

import anyio.to_thread
import httpx
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.types import Scope

from opncafyr import __version__
from opncafyr.core.bootstrap import OPEN_PATH, consume_open_token, tokens_dir
from opncafyr.core.datasets import DatasetService
from opncafyr.core.datasets_api import mount_datasets_api
from opncafyr.core.health import ToolHealth
from opncafyr.core.jobmanager import JOB_ID_RE, JobManager
from opncafyr.core.lifecycle import (
    DEFAULT_THEME,
    MAX_IDLE_MINUTES,
    MIN_IDLE_MINUTES,
    SETTINGS_FILE,
    THEMES,
    Lifecycle,
    LifecycleConfig,
    Settings,
    minutes_to_seconds,
)
from opncafyr.core.picker import pick_folder
from opncafyr.core.proxy import mount_proxy
from opncafyr.core.registry import (
    ToolDescriptor,
    WorkflowPack,
    load_descriptors,
    load_web_adapters,
    load_workflows,
)
from opncafyr.core.runtime import Runtime, load_runtime
from opncafyr.core.security import (
    REOPEN_PAGE,
    LoopbackGuardMiddleware,
    SecurityConfig,
    reopen_page_headers,
)
from opncafyr.core.supervisor import DEFAULT_MAX_BODY_BYTES, Supervisor, WebAppConfig
from opncafyr.core.workspace import RECENT_FILE, RecentWorkspaces
from opncafyr.core.workspace_api import Picker, mount_workspace_api
from opncafyr.paths import logs_dir, state_dir

log = logging.getLogger(__name__)

SESSION_PLACEHOLDER = "{{OPNCAFYR_SESSION}}"
THEME_PLACEHOLDER = "{{OPNCAFYR_THEME}}"

# The home page loads only its own files and talks only to its own origin (ADR 0006).
HOME_CSP = "; ".join(
    [
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self'",
        "connect-src 'self'",
        "img-src 'self' data:",
        "object-src 'none'",
        "base-uri 'none'",
        "form-action 'self'",
        "frame-ancestors 'none'",
    ]
)


class HomeAssets(StaticFiles):
    """The home page's files under ``/static/``. ``no-cache`` makes the browser revalidate,
    so an upgraded launcher never pairs a new page with old scripts."""

    async def get_response(self, path: str, scope: Scope):  # type: ignore[override]
        response = await super().get_response(path, scope)
        response.headers["cache-control"] = "no-cache"
        return response


# Children may take a while to answer (an NCA calculation, a deck build request), but a
# connection to a local child either opens at once or not at all.
PROXY_TIMEOUT = httpx.Timeout(connect=5.0, read=300.0, write=300.0, pool=10.0)


class PageBody(BaseModel):
    page_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")


class QuitBody(BaseModel):
    # The spec's default: keep live jobs running and stop once they finish.
    jobs: Literal["keep", "cancel"] = "keep"
    unsaved: Literal["refuse", "save"] = "refuse"


class SettingsBody(BaseModel):
    # Send only the settings to change; an explicit null idle timeout turns it off.
    idle_timeout_minutes: int | None = Field(default=None, ge=MIN_IDLE_MINUTES, le=MAX_IDLE_MINUTES)
    theme: Literal["system", "light", "dark"] | None = None


async def _monitor(lifecycle: Lifecycle, tick_s: float) -> None:
    while not lifecycle.stopped.is_set():
        await asyncio.sleep(tick_s)
        try:
            await anyio.to_thread.run_sync(lifecycle.check)
        except Exception:
            log.exception("Lifecycle check failed")


def build_supervisor(tools: dict[str, ToolDescriptor], runtime: Runtime) -> Supervisor:
    adapters = load_web_adapters(tools)
    apps = {
        tool_id: WebAppConfig(
            adapter=adapter,
            route=tools[tool_id].web_route or f"/apps/{tool_id}/",
            max_body_bytes=tools[tool_id].max_body_bytes or DEFAULT_MAX_BODY_BYTES,
        )
        for tool_id, adapter in adapters.items()
    }
    return Supervisor(
        apps, runtime=runtime, state_root=state_dir(), log_root=logs_dir() / "children"
    )


def create_app(
    security: SecurityConfig,
    request_shutdown: Callable[[], None] | None = None,
    supervisor: Supervisor | None = None,
    jobs: JobManager | None = None,
    picker: Picker | None = None,
    recent: RecentWorkspaces | None = None,
    instance_id: str | None = None,
    lifecycle: LifecycleConfig | None = None,
    settings: Settings | None = None,
    tools: dict[str, ToolDescriptor] | None = None,
    runtime: Runtime | None = None,
    open_tokens: Path | None = None,
    workflows: list[WorkflowPack] | None = None,
) -> FastAPI:
    """Build the controller app.

    Descriptors are loaded here, before anything listens, so an invalid one stops the
    launch with a ``DescriptorError`` naming the file and field.
    """
    # Lets a second launch confirm that /health on the recorded port is this instance.
    instance_id = instance_id or uuid.uuid4().hex
    lifecycle_config = lifecycle or LifecycleConfig()
    installed = tools if tools is not None else load_descriptors()
    packs = workflows if workflows is not None else load_workflows()
    datasets = DatasetService(
        importers=[i for p in packs for i in p.importers],
        annotators=[a for p in packs for a in p.annotators],
    )
    token_folder = open_tokens if open_tokens is not None else tokens_dir(state_dir())

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.tools = installed
        rt = runtime if runtime is not None else load_runtime()
        health = ToolHealth(installed, rt)
        app.state.health = health
        health.start()
        sup = supervisor if supervisor is not None else build_supervisor(installed, rt)
        app.state.supervisor = sup
        # Opening a workspace (/api/workspace/open) runs the job sweep.
        manager = jobs if jobs is not None else JobManager(runtime=rt)
        app.state.jobs = manager
        app.state.workspace = None
        store = settings if settings is not None else Settings(state_dir() / SETTINGS_FILE)
        app.state.settings = store
        if lifecycle_config.override_idle:
            idle_s = lifecycle_config.idle_timeout_s
        else:
            idle_s = minutes_to_seconds(store.idle_timeout_minutes())
        life = Lifecycle(
            jobs=manager,
            activity=sup.activity,
            stop_children=sup.stop_all,
            request_shutdown=request_shutdown or (lambda: None),
            idle_timeout_s=idle_s,
            heartbeat_window_s=lifecycle_config.heartbeat_window_s,
        )
        app.state.lifecycle = life
        # In case the server dies without running shutdown (atexit runs these in reverse).
        atexit.register(health.stop)
        atexit.register(sup.stop_all)
        atexit.register(manager.shutdown)
        async with httpx.AsyncClient(timeout=PROXY_TIMEOUT, follow_redirects=False) as client:
            app.state.proxy_client = client
            monitor = asyncio.create_task(_monitor(life, lifecycle_config.tick_s))
            try:
                yield
            finally:
                monitor.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await monitor
                await anyio.to_thread.run_sync(manager.shutdown)
                await anyio.to_thread.run_sync(sup.stop_all)
                await anyio.to_thread.run_sync(health.stop)
                atexit.unregister(manager.shutdown)
                atexit.unregister(sup.stop_all)
                atexit.unregister(health.stop)

    app = FastAPI(
        title="opncafyr",
        version=__version__,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.add_middleware(LoopbackGuardMiddleware, config=security)

    started = time.monotonic()

    @app.get("/health")
    def health() -> dict[str, object]:
        # No paths, workspace names or data: anyone on this machine can read this.
        return {
            "status": "ok",
            "version": __version__,
            "instance_id": instance_id,
            "uptime_s": round(time.monotonic() - started, 3),
        }

    @app.get("/api/tools")
    def list_tools() -> dict[str, list[dict[str, object]]]:
        health: ToolHealth = app.state.health
        return {
            "tools": [
                {
                    "id": t.id,
                    "display_name": t.display_name or t.id,
                    "version": t.version,
                    "capabilities": list(t.capabilities),
                    "web_route": t.web_route,
                    **health.status(t.id).as_json(),
                }
                for t in app.state.tools.values()
            ]
        }

    @app.get("/api/apps")
    def apps() -> dict[str, list[dict[str, object]]]:
        sup: Supervisor = app.state.supervisor
        return {
            "apps": [
                {"id": tool, "route": sup.apps[tool].route, "state": str(sup.state(tool))}
                for tool in sup.tools
            ]
        }

    def job_manager() -> JobManager:
        return app.state.jobs

    def checked_job(job_id: str) -> dict[str, object]:
        if not JOB_ID_RE.match(job_id):
            raise HTTPException(status_code=404, detail="Unknown job.")
        try:
            return job_manager().get(job_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="Unknown job.") from None

    @app.get("/api/jobs")
    def list_jobs() -> dict[str, object]:
        manager = job_manager()
        return {"workspace_open": manager.workspace is not None, "jobs": manager.list()}

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str, after: int = Query(default=0, ge=0)) -> dict[str, object]:
        job = checked_job(job_id)
        return {"job": job, "events": job_manager().events(job_id, after=after)}

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel_job(job_id: str) -> dict[str, object]:
        checked_job(job_id)
        job_manager().cancel(job_id)
        return {"job": job_manager().get(job_id)}

    def lifecycle_of() -> Lifecycle:
        return app.state.lifecycle

    @app.post("/api/session/heartbeat")
    def heartbeat(body: PageBody) -> dict[str, bool]:
        lifecycle_of().heartbeat(body.page_id)
        return {"ok": True}

    @app.post("/api/session/leave")
    def leave(body: PageBody) -> dict[str, bool]:
        lifecycle_of().leave(body.page_id)
        return {"ok": True}

    @app.get("/api/lifecycle")
    def lifecycle_status() -> dict[str, object]:
        return lifecycle_of().status()

    @app.put("/api/settings")
    def put_settings(body: SettingsBody) -> dict[str, object]:
        sent = body.model_fields_set
        if not sent:
            raise HTTPException(status_code=422, detail="No setting to change.")
        store = app.state.settings
        if "idle_timeout_minutes" in sent:
            store.set_idle_timeout_minutes(body.idle_timeout_minutes)
            lifecycle_of().idle_timeout_s = minutes_to_seconds(body.idle_timeout_minutes)
        if "theme" in sent:
            if body.theme is None:
                raise HTTPException(status_code=422, detail="Choose system, light or dark.")
            store.set_theme(body.theme)
        return {"idle_timeout_minutes": store.idle_timeout_minutes(), "theme": store.theme()}

    @app.post("/api/quit")
    def quit_app(body: QuitBody | None = None) -> dict[str, object]:
        choice = body or QuitBody()
        result = lifecycle_of().request_quit(jobs=choice.jobs, unsaved=choice.unsaved)
        if result.status == "needs_choice":
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "needs_choice",
                    "message": "There are unsaved changes. Save them in the editor, "
                    "or choose Save and quit.",
                    "reasons": result.reasons,
                    "tools": result.tools,
                },
            )
        return {"status": result.status, "jobs": result.jobs, "busy": result.tools}

    @app.post("/api/quit/abort")
    def abort_quit() -> dict[str, bool]:
        if not lifecycle_of().abort_quit():
            raise HTTPException(status_code=409, detail="The launcher is already stopping.")
        return {"ok": True}

    @app.get(OPEN_PATH)
    def open_session(request: Request, token: str = "") -> Response:
        """Trade a one-time token (core.bootstrap) for the session cookie."""
        has_session = security.secret_matches(request.cookies.get(security.cookie_name))
        if not consume_open_token(token_folder, token) and not has_session:
            headers = {k.decode(): v.decode() for k, v in reopen_page_headers()}
            return HTMLResponse(REOPEN_PAGE, status_code=403, headers=headers)
        response = RedirectResponse(
            "/",
            status_code=303,
            headers={"cache-control": "no-store", "referrer-policy": "no-referrer"},
        )
        response.set_cookie(
            security.cookie_name,
            security.secret,
            path="/",
            httponly=True,
            samesite="strict",
        )
        return response

    @app.get("/", response_class=HTMLResponse)
    def home() -> HTMLResponse:
        page = resources.files("opncafyr.web").joinpath("static/index.html").read_text("utf-8")
        # Only a browser holding the session cookie gets here, and no CORS headers are
        # ever sent. The page sends the secret back as a header on state-changing calls.
        # The saved theme goes on <html> so the first paint is already in the right theme.
        theme = app.state.settings.theme()
        if theme not in THEMES:
            theme = DEFAULT_THEME
        page = page.replace(SESSION_PLACEHOLDER, escape(security.secret))
        return HTMLResponse(
            page.replace(THEME_PLACEHOLDER, theme),
            headers={
                "cache-control": "no-store",
                "content-security-policy": HOME_CSP,
                "referrer-policy": "no-referrer",
            },
        )

    mount_workspace_api(
        app,
        picker=picker if picker is not None else pick_folder,
        recent=recent if recent is not None else RecentWorkspaces(state_dir() / RECENT_FILE),
    )
    mount_datasets_api(app, datasets)

    @app.get("/api/workflows")
    def workflow_views() -> dict[str, Any]:
        """The pages each workflow pack adds to the shell; core never knows what they do."""
        return {
            "workflows": [
                {
                    "id": pack.id,
                    "views": [
                        {
                            "id": v.id,
                            "title": v.title,
                            "icon": v.icon,
                            "summary": v.summary,
                            "module": f"static/workflows/{pack.id}/{v.module}",
                        }
                        for v in pack.views
                    ],
                }
                for pack in packs
            ]
        }

    for pack in packs:
        if pack.mount is not None:
            pack.mount(app)
    mount_proxy(app)
    for pack in packs:
        if pack.static is not None:
            app.mount(
                f"/static/workflows/{pack.id}",
                HomeAssets(packages=[pack.static]),
                name=f"workflow-{pack.id}",
            )
    app.mount("/static", HomeAssets(packages=[("opncafyr.web", "static")]), name="static")
    return app
