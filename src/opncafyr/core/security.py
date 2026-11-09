"""Loopback security boundary for the controller.

The local URL is still a security boundary (docs/spec.md, "One local origin owns every app").
Other processes on this machine, including other users' on a shared host, and any web page
the user visits can reach 127.0.0.1. This module enforces:

- Host header must be the loopback authority we bound to (blocks DNS rebinding).
- When a browser sends an Origin, it must be our own origin.
- Every request except ``/health`` and ``/open`` must carry the per-launch session cookie
  (``HttpOnly; SameSite=Strict; Path=/``, named after the port). The browser gets it only
  from ``/open`` with a one-time token (``core.bootstrap``), so the home page, its secret
  and the API are closed to anyone who can merely connect.
- ``Sec-Fetch-Site``, when sent, must be ``same-origin`` or ``none``. Any other loopback
  port counts as same-site, so ``same-site`` is refused too.
- State-changing requests also need the session secret in the ``x-opncafyr-session``
  header (the home page reads it from a meta tag). Proxied child apps under ``/apps/``
  can't send a custom header, so there they need our own ``Origin`` instead.
- No CORS headers are ever added, so other origins cannot read responses.

Cloud container mode (#42, ``core.remote``) only *adds* the forward's or IDE proxy's own
Host and Origin to the allow-lists, scopes the cookie to the URL prefix, and makes it
``Secure`` behind https. Every check above still applies.
"""

from __future__ import annotations

import hmac
import secrets
from dataclasses import dataclass, field

from starlette.types import ASGIApp, Receive, Scope, Send

SESSION_HEADER = "x-opncafyr-session"
# Open without the cookie: the instance probe and the one-time token exchange.
PUBLIC_PATHS = frozenset({"/health", "/open"})
SESSION_COOKIE_PREFIX = "opncafyr_session_"
PROXY_PREFIX = "/apps/"
ALLOWED_FETCH_SITES = frozenset({"same-origin", "none"})
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
LOOPBACK_HOSTS = ("127.0.0.1", "localhost")


def new_session_secret() -> str:
    """A fresh random secret generated at every launch."""
    return secrets.token_urlsafe(32)


@dataclass(frozen=True)
class SecurityConfig:
    port: int
    secret: str = field(default_factory=new_session_secret, repr=False)
    # Cloud container mode only (core.remote): the browser-side Host and Origin values of
    # an SSH forward or IDE proxy, the URL prefix, and the cookie's name and Secure flag.
    extra_hosts: frozenset[str] = frozenset()
    extra_origins: frozenset[str] = frozenset()
    base_path: str = "/"
    cookie_id: str | None = None
    secure_cookie: bool = False

    def __post_init__(self) -> None:
        if not (self.base_path.startswith("/") and self.base_path.endswith("/")):
            raise ValueError("base_path must start and end with '/'")

    @property
    def allowed_hosts(self) -> frozenset[str]:
        return frozenset(f"{h}:{self.port}" for h in LOOPBACK_HOSTS) | self.extra_hosts

    @property
    def allowed_origins(self) -> frozenset[str]:
        loopback = frozenset(f"http://{h}:{self.port}" for h in LOOPBACK_HOSTS)
        return loopback | self.extra_origins

    @property
    def cookie_name(self) -> str:
        # Cookies ignore ports, so the port keeps two launchers from sharing one cookie.
        return f"{SESSION_COOKIE_PREFIX}{self.cookie_id or self.port}"

    def secret_matches(self, candidate: str | None) -> bool:
        if not candidate:
            return False
        return hmac.compare_digest(candidate.encode(), self.secret.encode())


class LoopbackGuardMiddleware:
    """Pure ASGI middleware applying the Host, Origin and session-secret checks."""

    def __init__(self, app: ASGIApp, config: SecurityConfig) -> None:
        self.app = app
        self.config = config

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}

        if headers.get("host") not in self.config.allowed_hosts:
            await _reject(scope, receive, send, 400, "Invalid Host header.")
            return

        origin = headers.get("origin")
        if origin is not None and origin not in self.config.allowed_origins:
            await _reject(scope, receive, send, 403, "Cross-origin request rejected.")
            return

        method = scope.get("method", "GET")
        state_changing = scope["type"] == "websocket" or method not in SAFE_METHODS
        path = scope.get("path", "")

        if path in PUBLIC_PATHS and scope["type"] == "http" and not state_changing:
            if path != "/health" and not _fetch_site_ok(headers):
                await _reject(scope, receive, send, 403, "Cross-site request rejected.")
                return
            await self.app(scope, receive, send)
            return

        if not _fetch_site_ok(headers):
            await _reject(scope, receive, send, 403, "Cross-site request rejected.")
            return

        cookie = parse_cookies(headers.get("cookie", "")).get(self.config.cookie_name)
        if not self.config.secret_matches(cookie):
            await _reject(scope, receive, send, 403, "Missing or invalid session cookie.")
            return

        if path.startswith(PROXY_PREFIX):
            if state_changing and origin is None:
                await _reject(scope, receive, send, 403, "Missing Origin header.")
                return
        elif state_changing and not self.config.secret_matches(headers.get(SESSION_HEADER)):
            await _reject(scope, receive, send, 403, "Missing or invalid session token.")
            return

        await self.app(scope, receive, send)


class BasePathMiddleware:
    """Serve the app under ``base_path`` (an IDE proxy's ``/proxy/<port>/``, #42).

    Some proxies strip the prefix before forwarding and some don't; a path under the
    prefix loses it here, and any other path is served as it is. Runs outside
    ``LoopbackGuardMiddleware``, so every check sees the app's own paths.
    """

    def __init__(self, app: ASGIApp, base_path: str) -> None:
        self.app = app
        self.prefix = base_path.rstrip("/")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if self.prefix and scope["type"] in ("http", "websocket"):
            path: str = scope.get("path", "")
            if path == self.prefix or path.startswith(self.prefix + "/"):
                scope = dict(scope)
                scope["path"] = path[len(self.prefix) :] or "/"
                raw = scope.get("raw_path")
                if isinstance(raw, bytes):
                    scope["raw_path"] = raw[len(self.prefix.encode()) :] or b"/"
        await self.app(scope, receive, send)


def _fetch_site_ok(headers: dict[str, str]) -> bool:
    site = headers.get("sec-fetch-site")
    return site is None or site in ALLOWED_FETCH_SITES


def parse_cookies(header: str) -> dict[str, str]:
    """Lenient Cookie parsing: other apps on 127.0.0.1 share this cookie jar."""
    cookies: dict[str, str] = {}
    for part in header.split(";"):
        name, sep, value = part.strip().partition("=")
        if sep and name and name not in cookies:
            cookies[name] = value.strip().strip('"')
    return cookies


# Shown when a page is opened without a valid session (an old tab, a copied URL, a used
# token). It carries no secret and no data.
REOPEN_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>opncafyr</title>
<meta name="viewport" content="width=device-width, initial-scale=1"></head>
<body><main><h1>Open opncafyr from its shortcut</h1>
<p>This page is not connected to a running opncafyr session. It may be from an earlier
session, or the link was already used.</p>
<p>Open opncafyr again from its desktop shortcut (or run <code>opncafyr launch</code>).
If opncafyr is still running, this reopens it.</p></main></body></html>
"""


def reopen_page_headers() -> list[tuple[bytes, bytes]]:
    return [
        (b"content-type", b"text/html; charset=utf-8"),
        (b"cache-control", b"no-store"),
        (b"content-security-policy", b"default-src 'none'; frame-ancestors 'none'"),
        (b"referrer-policy", b"no-referrer"),
    ]


async def _reject(scope: Scope, receive: Receive, send: Send, status: int, detail: str) -> None:
    if scope["type"] == "websocket":
        await send({"type": "websocket.close", "code": 1008})
        return
    if scope.get("path") == "/" and scope.get("method") in ("GET", "HEAD") and status == 403:
        body = REOPEN_PAGE.encode()
        headers = reopen_page_headers()
    else:
        body = ('{"detail":"' + detail + '"}').encode()
        headers = [(b"content-type", b"application/json")]
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [*headers, (b"content-length", str(len(body)).encode())],
        }
    )
    await send({"type": "http.response.body", "body": body})
