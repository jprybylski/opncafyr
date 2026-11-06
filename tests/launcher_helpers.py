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
from opncafyr.core.instance import read_record
from opncafyr.core.security import SESSION_HEADER


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
        page = httpx.get(url).text
        secret = re.search(r'name="opncafyr-session" content="([^"]+)"', page).group(1)
        httpx.post(f"{url}api/quit", headers={SESSION_HEADER: secret})
        thread.join(30)
        assert not thread.is_alive()
        assert result["rc"] == 0
