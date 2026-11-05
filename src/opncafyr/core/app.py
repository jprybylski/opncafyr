"""FastAPI application factory for the loopback controller.

One origin serves the home page, the API and (later) proxied child apps under
``/apps/<tool>/``. The lifespan hook is where child-process supervision will live.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from html import escape
from importlib import resources

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from opncafyr import __version__
from opncafyr.core.registry import load_descriptors
from opncafyr.core.security import LoopbackGuardMiddleware, SecurityConfig

SESSION_PLACEHOLDER = "{{OPNCAFYR_SESSION}}"


def create_app(
    security: SecurityConfig,
    request_shutdown: Callable[[], None] | None = None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.tools = load_descriptors()
        # Child apps (opnca-r, deckifyr) will be started on demand and stopped here.
        yield

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

    @app.post("/api/quit")
    def quit_app() -> dict[str, str]:
        # Gate 1 adds the "keep running jobs vs cancel" prompt and unsaved-editor checks.
        if request_shutdown is not None:
            request_shutdown()
        return {"status": "stopping"}

    @app.get("/", response_class=HTMLResponse)
    def home() -> str:
        page = resources.files("opncafyr.web").joinpath("static/index.html").read_text("utf-8")
        # The secret is readable only by same-origin pages: no CORS headers are ever sent.
        return page.replace(SESSION_PLACEHOLDER, escape(security.secret))

    return app
