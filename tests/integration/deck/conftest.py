"""Fixtures for the real deckifyr tests (#33). Skipped unless the runtime has deckifyr."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from opncafyr.core.runtime import RUNTIME_FILE_ENV, load_runtime

pytestmark = pytest.mark.integration


@pytest.fixture(scope="session")
def runtime():
    path = os.environ.get(RUNTIME_FILE_ENV)
    if not path:
        pytest.skip("OPNCAFYR_RUNTIME_FILE is not set (see docs/spikes/basepath.md)")
    rt = load_runtime(Path(path))
    if not rt.has("deckifyr_python"):
        pytest.skip("the runtime has no deckifyr")
    return rt
