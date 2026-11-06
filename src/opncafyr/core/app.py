"""FastAPI application factory for the loopback controller.

One origin serves the home page, the API and the proxied child apps under
``/apps/<tool>/``. The lifespan owns the child supervisor and the job manager: children
and jobs are started by workflows and adapters, and all of them are stopped when the
controller exits. The user picks the study workspace through ``/api/workspace/*``
(``core.workspace_api``); opening one points the job manager at it.
"""

from __future__ import annotations

import atexit
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from html import escape
from importlib import resources

import anyio.to_thread
import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse

from opncafyr import __version__
from opncafyr.core.jobmanager import JOB_ID_RE, JobManager
from opncafyr.core.picker import pick_folder
from opncafyr.core.proxy import mount_proxy
from opncafyr.core.registry import ToolDescriptor, load_descriptors, load_web_adapters
from opncafyr.core.runtime import load_runtime
from opncafyr.core.security import PROXY_PREFIX, LoopbackGuardMiddleware, SecurityConfig
from opncafyr.core.supervisor import DEFAULT_MAX_BODY_BYTES, Supervisor, WebAppConfig
from opncafyr.core.workspace import RECENT_FILE, RecentWorkspaces
from opncafyr.core.workspace_api import Picker, mount_workspace_api
from opncafyr.paths import logs_dir, state_dir

SESSION_PLACEHOLDER = "{{OPNCAFYR_SESSION}}"

# Children may take a while to answer (an NCA calculation, a deck build request), but a
# connection to a local child either opens at once or not at all.
PROXY_TIMEOUT = httpx.Timeout(connect=5.0, read=300.0, write=300.0, pool=10.0)


def build_supervisor(tools: dict[str, ToolDescriptor]) -> Supervisor:
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
        apps, runtime=load_runtime(), state_root=state_dir(), log_root=logs_dir() / "children"
    )


def create_app(
    security: SecurityConfig,
    request_shutdown: Callable[[], None] | None = None,
    supervisor: Supervisor | None = None,
    jobs: JobManager | None = None,
    picker: Picker | None = None,
    recent: RecentWorkspaces | None = None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.tools = load_descriptors()
        sup = supervisor if supervisor is not None else build_supervisor(app.state.tools)
        app.state.supervisor = sup
        # Opening a workspace (/api/workspace/open) runs the job sweep.
        manager = jobs if jobs is not None else JobManager(runtime=load_runtime())
        app.state.jobs = manager
        app.state.workspace = None
        # In case the server dies without running shutdown (atexit runs these in reverse).
        atexit.register(sup.stop_all)
        atexit.register(manager.shutdown)
        async with httpx.AsyncClient(timeout=PROXY_TIMEOUT, follow_redirects=False) as client:
            app.state.proxy_client = client
            try:
                yield
            finally:
                await anyio.to_thread.run_sync(manager.shutdown)
                await anyio.to_thread.run_sync(sup.stop_all)
                atexit.unregister(manager.shutdown)
                atexit.unregister(sup.stop_all)

    app = FastAPI(
        title="opncafyr",
        version=__version__,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.add_middleware(LoopbackGuardMiddleware, config=security)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @app.get("/api/tools")
    def tools() -> dict[str, list[dict[str, object]]]:
        return {
            "tools": [
                {"id": t.id, "capabilities": list(t.capabilities), "web_route": t.web_route}
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

    @app.post("/api/quit")
    def quit_app() -> dict[str, str]:
        # Gate 1 adds the "keep running jobs vs cancel" prompt and unsaved-editor checks.
        if request_shutdown is not None:
            request_shutdown()
        return {"status": "stopping"}

    @app.get("/", response_class=HTMLResponse)
    def home() -> HTMLResponse:
        page = resources.files("opncafyr.web").joinpath("static/index.html").read_text("utf-8")
        # The secret is readable only by same-origin pages: no CORS headers are ever sent.
        response = HTMLResponse(
            page.replace(SESSION_PLACEHOLDER, escape(security.secret)),
            headers={"cache-control": "no-store"},
        )
        # Proxied child pages can't send the session header, so they carry this cookie.
        response.set_cookie(
            security.cookie_name,
            security.secret,
            path=PROXY_PREFIX,
            httponly=True,
            samesite="strict",
        )
        return response

    mount_workspace_api(
        app,
        picker=picker if picker is not None else pick_folder,
        recent=recent if recent is not None else RecentWorkspaces(state_dir() / RECENT_FILE),
    )
    mount_proxy(app)
    return app
