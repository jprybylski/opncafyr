"""Loopback security boundary for the controller.

The local URL is still a security boundary (docs/spec.md, "One local origin owns every app").
This module enforces:

- Host header must be the loopback authority we bound to (blocks DNS rebinding).
- State-changing requests must carry the per-launch session secret (blocks CSRF) and,
  when the browser sends an Origin, it must be our own origin.
- Proxied child apps under ``/apps/`` can't send a custom header from navigations and
  asset loads, so there the secret travels in an ``HttpOnly; SameSite=Strict;
  Path=/apps/`` cookie (accepted on #15). Every request needs it, ``Sec-Fetch-Site``
  must be ``same-origin`` or ``none`` (any other loopback port counts as same-site), and
  state-changing requests need our own ``Origin``.
- No CORS headers are ever added, so other origins cannot read responses.
"""

from __future__ import annotations

import hmac
import secrets
from dataclasses import dataclass, field

from starlette.types import ASGIApp, Receive, Scope, Send

SESSION_HEADER = "x-opncafyr-session"
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

    @property
    def allowed_hosts(self) -> frozenset[str]:
        return frozenset(f"{h}:{self.port}" for h in LOOPBACK_HOSTS)

    @property
    def allowed_origins(self) -> frozenset[str]:
        return frozenset(f"http://{h}" for h in self.allowed_hosts)

    @property
    def cookie_name(self) -> str:
        # Cookies ignore ports, so the port keeps two launchers from sharing one cookie.
        return f"{SESSION_COOKIE_PREFIX}{self.port}"

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

        if scope.get("path", "").startswith(PROXY_PREFIX):
            cookie = parse_cookies(headers.get("cookie", "")).get(self.config.cookie_name)
            if not self.config.secret_matches(cookie):
                await _reject(scope, receive, send, 403, "Missing or invalid session cookie.")
                return
            site = headers.get("sec-fetch-site")
            if site is not None and site not in ALLOWED_FETCH_SITES:
                await _reject(scope, receive, send, 403, "Cross-site request rejected.")
                return
            if state_changing and origin is None:
                await _reject(scope, receive, send, 403, "Missing Origin header.")
                return
            await self.app(scope, receive, send)
            return

        if state_changing and not self.config.secret_matches(headers.get(SESSION_HEADER)):
            await _reject(scope, receive, send, 403, "Missing or invalid session token.")
            return

        await self.app(scope, receive, send)


def parse_cookies(header: str) -> dict[str, str]:
    """Lenient Cookie parsing: other apps on 127.0.0.1 share this cookie jar."""
    cookies: dict[str, str] = {}
    for part in header.split(";"):
        name, sep, value = part.strip().partition("=")
        if sep and name and name not in cookies:
            cookies[name] = value.strip().strip('"')
    return cookies


async def _reject(scope: Scope, receive: Receive, send: Send, status: int, detail: str) -> None:
    if scope["type"] == "websocket":
        await send({"type": "websocket.close", "code": 1008})
        return
    body = ('{"detail":"' + detail + '"}').encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})
