"""One-time open tokens: how a browser gets the session cookie (#11).

Every request except ``/health`` and ``/open`` needs the session cookie, so a process
that can reach 127.0.0.1 (another user on a shared host, a web page) can't load the home
page or read the API. The launcher, and a second launch that reopens the running one,
both open the browser through ``/open?token=…``:

- ``mint_open_token`` makes a random token and stores only its SHA-256 as a file name in
  ``<state>/open-tokens/`` (a user-only folder in the app state dir).
- ``/open`` consumes the token: it must exist and be younger than ``TOKEN_TTL_S``. The
  file is renamed away first, so two requests can't both use it. The response sets the
  ``HttpOnly; SameSite=Strict; Path=/`` session cookie and redirects to ``/``, so the
  token leaves the address bar at once.

The session secret itself never appears in a URL, a file or the instance record.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import re
import secrets
import time
import uuid
from pathlib import Path

TOKENS_DIR = "open-tokens"
TOKEN_TTL_S = 120.0
OPEN_PATH = "/open"
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{20,128}$")


def tokens_dir(state_dir: Path) -> Path:
    return Path(state_dir) / TOKENS_DIR


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def mint_open_token(folder: Path) -> str:
    """Create a one-time token in ``folder`` and return it."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    sweep(folder)
    token = secrets.token_urlsafe(32)
    fd = os.open(folder / _digest(token), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="ascii") as fh:
        fh.write(repr(time.time()))
    return token


def consume_open_token(folder: Path, token: str | None, *, now: float | None = None) -> bool:
    """Accept ``token`` once, if it was minted in the last ``TOKEN_TTL_S`` seconds."""
    if not token or not _TOKEN_RE.match(token):
        return False
    path = Path(folder) / _digest(token)
    claimed = path.with_name(f".{path.name}.{uuid.uuid4().hex}.used")
    try:
        os.replace(path, claimed)  # only one caller wins the rename
    except OSError:
        return False
    try:
        minted = float(claimed.read_text("ascii"))
    except (OSError, ValueError):
        return False
    finally:
        claimed.unlink(missing_ok=True)
    age = (time.time() if now is None else now) - minted
    return 0 <= age <= TOKEN_TTL_S


def sweep(folder: Path, *, now: float | None = None) -> None:
    """Remove expired and leftover token files."""
    current = time.time() if now is None else now
    with contextlib.suppress(OSError):
        for entry in Path(folder).iterdir():
            with contextlib.suppress(OSError):
                if current - entry.stat().st_mtime > TOKEN_TTL_S:
                    entry.unlink()


def open_url(base_url: str, token: str) -> str:
    """``http://127.0.0.1:<port>/open?token=…`` for a base URL ending in ``/``."""
    return f"{base_url.rstrip('/')}{OPEN_PATH}?token={token}"
