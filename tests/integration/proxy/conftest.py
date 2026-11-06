"""Fixtures for the #16 proxy harness. Skipped unless OPNCAFYR_RUNTIME_FILE is set."""

from __future__ import annotations

import pytest
from proxy_harness import Controller, harness_runtime


@pytest.fixture(scope="session")
def runtime():
    rt = harness_runtime()
    if rt is None:
        pytest.skip("OPNCAFYR_RUNTIME_FILE is not set (see docs/spikes/basepath.md)")
    return rt


@pytest.fixture(scope="module")
def controller(runtime, tmp_path_factory):
    ctl = Controller(root=tmp_path_factory.mktemp("controller"), runtime=runtime)
    yield ctl
    ctl.close()


@pytest.fixture(scope="session")
def browser():
    sync_api = pytest.importorskip("playwright.sync_api")
    with sync_api.sync_playwright() as pw:
        chromium = pw.chromium.launch()
        yield chromium
        chromium.close()
