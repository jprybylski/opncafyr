"""Fixtures for the real quartifyr render tests (#32). Skipped without a full runtime."""

from __future__ import annotations

import pytest

from nca_helpers import integration_runtime

pytestmark = pytest.mark.integration


@pytest.fixture(scope="session")
def runtime():
    rt = integration_runtime()
    if rt is None:
        pytest.skip("OPNCAFYR_RUNTIME_FILE is not set (see docs/spikes/basepath.md)")
    missing = [n for n in ("quarto", "uv", "deckifyr_python") if not rt.has(n)]
    if missing or not (rt.r_library / "quartifyr").is_dir():
        pytest.skip(
            f"the runtime has no quartifyr report stack ({', '.join(missing) or 'R package'})"
        )
    return rt


@pytest.fixture(autouse=True)
def app_root(tmp_path, monkeypatch):
    # The uv and Deno caches go under the app root; keep them out of the real one.
    monkeypatch.setenv("OPNCAFYR_APP_ROOT", str(tmp_path / "app"))
