"""JSON records the installer writes: atomically, via ``core.manifest.atomic_write_json``.

The installer runs before jsonschema is installed, so it writes without validating; the
self-check (``selfcheck.py``), run with the new launcher environment, validates
``install.json`` (``install-v1``) and ``runtime.json`` (``runtime-v1``) before the install
is committed, and the tests validate every record against its schema.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from opncafyr.core.manifest import atomic_write_json


def write_json(path: Path, doc: Any) -> Path:
    return atomic_write_json(path, doc, schema_name=None)
