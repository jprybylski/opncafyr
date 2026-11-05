"""Loopback security boundary for the controller.

The local URL is still a security boundary (docs/spec.md, "One local origin owns every app").
This module enforces:

- Host header must be the loopback authority we bound to (blocks DNS rebinding).
- State-changing requests must carry the per-launch session secret (blocks CSRF) and,
  when the browser sends an Origin, it must be our own origin.
- No CORS headers are ever added, so other origins cannot read responses.
"""

from __future__ import annotations

import hmac
import secrets
from dataclasses import dataclass, field

from starlette.types import ASGIApp, Receive, Scope, Send

SESSION_HEADER = "x-opncafyr-session"
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
        if state_changing and not self.config.secret_matches(headers.get(SESSION_HEADER)):
            await _reject(scope, receive, send, 403, "Missing or invalid session token.")
            return

        await self.app(scope, receive, send)


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
