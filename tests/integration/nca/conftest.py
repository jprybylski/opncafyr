"""Fixtures for the R-backed NCA tests. Skipped unless OPNCAFYR_RUNTIME_FILE is set."""

from __future__ import annotations

import pytest

from nca_helpers import integration_runtime

pytestmark = pytest.mark.integration


@pytest.fixture(scope="session")
def runtime():
    rt = integration_runtime()
    if rt is None:
        pytest.skip("OPNCAFYR_RUNTIME_FILE is not set (see docs/spikes/basepath.md)")
    return rt
