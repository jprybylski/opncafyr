"""Reverse proxy for managed child apps under ``/apps/<tool>/`` (#15).

The children are configured with their own base path (opnca-r ``--base-path``, deckifyr
``--root-path``), so the full ``/apps/<tool>/...`` path is forwarded unchanged and no HTML
or ``Location`` rewriting is needed. The only rewrite is turning an absolute ``Location``
that points at the child's private origin into a path on ours.

Before this module runs, ``LoopbackGuardMiddleware`` has already checked the Host, the
Origin, ``Sec-Fetch-Site`` and the session cookie. Before forwarding, the proxy:

- sets ``Host`` to ``127.0.0.1:<child port>`` and drops ``Origin``, ``Referer``, ``Cookie``
  and the session header, so children need no ``--allowed-host`` and never see the
  launcher's secret;
- adds the child's own token header(s) and drops any inbound copy of them.

Responses lose ``Set-Cookie`` and every CORS header and gain
``Content-Security-Policy: frame-ancestors 'self'``: the launcher shows child pages in a
same-origin iframe (#50), and no other site may frame them. Every proxied request also
counts as an open page for idle shutdown. Bodies stream both ways, with a per-descriptor
request size limit. WebSockets are not proxied: neither child uses them.
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator
from html import escape

import anyio.to_thread
import httpx
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.background import BackgroundTask
from starlette.responses import StreamingResponse

from opncafyr.core.supervisor import Child, ChildState, LaunchError, Supervisor

METHODS = ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
CONTROL_SEGMENT = "_opncafyr"  # can't be a tool id: ids start with a letter

HOP_BY_HOP = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "proxy-connection",
        "te",
        "trailer",
        "trailers",
        "transfer-encoding",
        "upgrade",
    }
)
DROP_REQUEST = frozenset(
    {"host", "origin", "referer", "cookie", "x-opncafyr-session", "forwarded", "x-real-ip"}
)
DROP_RESPONSE = frozenset({"set-cookie", "set-cookie2"})

# The launcher shows child pages in a same-origin iframe (#50). Only our own pages may
# frame them; a child's own Content-Security-Policy still applies alongside this one.
FRAME_POLICY = ("content-security-policy", "frame-ancestors 'self'")

# A request through the proxy counts as an open page for idle shutdown, so a tool in use
# keeps the launcher running even when the home page is hidden in another tab.
APP_PAGE_PREFIX = "app-"


class BodyTooLarge(Exception):
    pass


def _connection_tokens(headers: httpx.Headers | dict[str, str]) -> set[str]:
    value = headers.get("connection", "")
    return {t.strip().lower() for t in value.split(",") if t.strip()}


def forward_request_headers(raw: list[tuple[bytes, bytes]], child: Child) -> list[tuple[str, str]]:
    auth = {k.lower() for k in child.spec.auth_headers}
    named = set()
    for k, v in raw:
        if k.decode("latin-1").lower() == "connection":
            named |= {t.strip().lower() for t in v.decode("latin-1").split(",")}
    out: list[tuple[str, str]] = []
    for k, v in raw:
        name = k.decode("latin-1").lower()
        if (
            name in HOP_BY_HOP
            or name in DROP_REQUEST
            or name in auth
            or name in named
            or name.startswith("x-forwarded-")
        ):
            continue
        out.append((name, v.decode("latin-1")))
    out.append(("host", f"127.0.0.1:{child.context.port}"))
    out += [(k, v) for k, v in child.spec.auth_headers.items()]
    return out


def filter_response_headers(headers: httpx.Headers, child: Child) -> list[tuple[str, str]]:
    named = _connection_tokens(headers)
    origins = (f"http://127.0.0.1:{child.context.port}", f"http://localhost:{child.context.port}")
    out: list[tuple[str, str]] = []
    for name, value in headers.multi_items():
        lname = name.lower()
        if (
            lname in HOP_BY_HOP
            or lname in DROP_RESPONSE
            or lname in named
            or lname.startswith("access-control-")
        ):
            continue
        if lname == "location":
            for origin in origins:
                if value.startswith(origin + "/"):
                    value = value[len(origin) :]
                    break
        out.append((lname, value))
    return out


def _page(status: int, title: str, body: str) -> HTMLResponse:
    html = (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        f"<title>{escape(title)}</title><style>"
        ":root{color-scheme:light dark;font-family:system-ui,sans-serif}"
        "body{max-width:40rem;margin:3rem auto;padding:0 1rem;line-height:1.5}"
        "</style></head><body>"
        f"<h1>{escape(title)}</h1>{body}"
        # _top: inside the launcher's tool view, go back to the launcher, not into a frame.
        "<p><a href='/' target='_top'>Back to the workspace home</a></p>"
        "</body></html>"
    )
    headers = {"cache-control": "no-store", FRAME_POLICY[0]: FRAME_POLICY[1]}
    return HTMLResponse(html, status_code=status, headers=headers)


def unavailable_page(sup: Supervisor, tool: str, detail: str | None = None) -> HTMLResponse:
    state = sup.state(tool)
    if state is ChildState.STARTING:
        return _page(503, f"{tool} is starting", "<p>Reload this page in a moment.</p>")
    if not sup.can_restart(tool):
        return _page(503, f"{tool} is not running", "<p>Open it from the workspace home.</p>")
    reason = escape(detail) if detail else "It stopped unexpectedly."
    form = (
        f"<p>{reason}</p>"
        f"<form method='post' action='/apps/{CONTROL_SEGMENT}/{escape(tool)}/restart'>"
        "<button type='submit'>Restart</button></form>"
    )
    return _page(502, f"{tool} is not responding", form)


def mount_proxy(app: FastAPI) -> None:
    """Add the ``/apps/...`` routes. ``app.state.supervisor`` and ``.proxy_client`` are set
    by the lifespan."""

    @app.post(f"/apps/{CONTROL_SEGMENT}/{{tool}}/restart")
    async def restart(request: Request, tool: str) -> Response:
        sup: Supervisor = request.app.state.supervisor
        if tool not in sup.apps:
            return JSONResponse({"detail": "Unknown app."}, status_code=404)
        if not sup.can_restart(tool):
            return unavailable_page(sup, tool)
        try:
            await anyio.to_thread.run_sync(sup.restart, tool)
        except LaunchError as exc:
            return unavailable_page(sup, tool, str(exc))
        return RedirectResponse(f"/apps/{tool}/", status_code=303)

    @app.api_route("/apps/{tool}", methods=METHODS, include_in_schema=False)
    async def add_slash(request: Request, tool: str) -> Response:
        query = request.url.query
        return RedirectResponse(f"/apps/{tool}/" + (f"?{query}" if query else ""), 308)

    @app.api_route("/apps/{tool}/{path:path}", methods=METHODS, include_in_schema=False)
    async def proxy(request: Request, tool: str, path: str) -> Response:
        sup: Supervisor = request.app.state.supervisor
        if tool not in sup.apps:
            return JSONResponse({"detail": "Unknown app."}, status_code=404)
        child = sup.get(tool)
        if child is None or child.state is not ChildState.RUNNING:
            return unavailable_page(sup, tool)
        life = getattr(request.app.state, "lifecycle", None)
        if life is not None:
            with contextlib.suppress(ValueError):  # a tool id that can't be a page id
                life.heartbeat(APP_PAGE_PREFIX + tool)

        limit = child.max_body_bytes
        declared = request.headers.get("content-length")
        if declared is not None and (not declared.isdigit() or int(declared) > limit):
            return JSONResponse({"detail": "Request body too large."}, status_code=413)

        has_body = declared is not None or "transfer-encoding" in request.headers

        async def body() -> AsyncIterator[bytes]:
            seen = 0
            async for chunk in request.stream():
                seen += len(chunk)
                if seen > limit:
                    raise BodyTooLarge
                yield chunk

        url = child.context.origin + request.url.path
        if request.url.query:
            url += "?" + request.url.query
        client: httpx.AsyncClient = request.app.state.proxy_client
        upstream = client.build_request(
            request.method,
            url,
            headers=forward_request_headers(request.scope["headers"], child),
            content=body() if has_body else None,
        )
        try:
            response = await client.send(upstream, stream=True)
        except BodyTooLarge:
            return JSONResponse({"detail": "Request body too large."}, status_code=413)
        except httpx.HTTPError as exc:
            if isinstance(exc.__cause__, BodyTooLarge) or isinstance(exc.__context__, BodyTooLarge):
                return JSONResponse({"detail": "Request body too large."}, status_code=413)
            return unavailable_page(sup, tool, "The app did not answer.")
        streamed = StreamingResponse(
            response.aiter_raw(),
            status_code=response.status_code,
            background=BackgroundTask(response.aclose),
        )
        # Raw headers keep repeated ones (Vary, Link, ...); we add only the frame policy.
        streamed.raw_headers = [
            (k.encode("latin-1"), v.encode("latin-1"))
            for k, v in [*filter_response_headers(response.headers, child), FRAME_POLICY]
        ]
        return streamed
