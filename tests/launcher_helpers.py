"""Run a real launcher in a thread for tests that need the whole controller."""

from __future__ import annotations

import contextlib
import re
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from opncafyr import cli
from opncafyr.core import instance
from opncafyr.core.bootstrap import mint_open_token, tokens_dir
from opncafyr.core.instance import read_record
from opncafyr.core.security import SESSION_HEADER


@contextlib.contextmanager
def open_session(state: Path, url: str) -> Iterator[httpx.Client]:
    """A browser stand-in that opened the launcher with a one-time token.

    It holds the session cookie and sends the secret and our Origin on every request.
    """
    with httpx.Client(base_url=url, timeout=30) as browser:
        token = mint_open_token(tokens_dir(state))
        resp = browser.get(f"open?token={token}")
        assert resp.status_code == 303, resp.text
        page = browser.get("").text
        secret = re.search(r'name="opncafyr-session" content="([^"]+)"', page).group(1)
        browser.headers.update({SESSION_HEADER: secret, "origin": url.rstrip("/")})
        yield browser


@contextlib.contextmanager
def running_controller(state: Path, **kwargs: Any) -> Iterator[dict[str, Any]]:
    """Run a real launcher in a thread; stop it through the API on exit."""
    result: dict[str, int] = {}
    thread = threading.Thread(
        target=lambda: result.setdefault(
            "rc", cli.run_controller(state=state, open_browser=False, claim_wait=5, **kwargs)
        ),
        daemon=True,
    )
    thread.start()
    deadline = time.monotonic() + 30
    record = None
    while time.monotonic() < deadline:
        record = read_record(state)
        if record and instance.probe(record["port"], record["instance_id"]):
            break
        time.sleep(0.05)
    else:
        pytest.fail("the launcher did not become healthy")
    url = instance.instance_url(record["port"])
    try:
        yield record
    finally:
        with open_session(state, url) as browser:
            browser.post("api/quit")
        thread.join(30)
        assert not thread.is_alive()
        assert result["rc"] == 0
