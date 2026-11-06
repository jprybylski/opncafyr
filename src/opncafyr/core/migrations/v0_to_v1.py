"""Schema 0 -> 1.0.

Schema 0 is the synthetic pre-release layout that the migration tests use. No released
opncafyr wrote it. Its shape:

    {"schema_version": "0",
     "created": "<ISO 8601>",
     "source_files": [{"path": "...", "sha256": "...", "bytes": 123}],
     "prefs": {...}}                       # optional

1.0 renames ``created`` to ``created_at``, ``source_files`` to ``sources`` (``bytes``
becomes ``size_bytes`` and each source gets an ``id``), and ``prefs`` to ``ui``. An
unknown field stops the migration instead of being dropped.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from opncafyr.core.migrations import MigrationContext

KNOWN = {"schema_version", "created", "source_files", "prefs"}
SOURCE_KNOWN = {"path", "sha256", "bytes"}


def apply(doc: dict[str, Any], ctx: MigrationContext) -> dict[str, Any]:
    unknown = set(doc) - KNOWN
    if unknown:
        raise ValueError(f"unknown schema 0 fields: {sorted(unknown)}")
    sources = []
    for n, entry in enumerate(doc.get("source_files", []), start=1):
        if set(entry) - SOURCE_KNOWN:
            raise ValueError("unknown schema 0 source fields")
        sources.append(
            {
                "id": f"source-{n}",
                "path": entry["path"],
                "sha256": entry["sha256"],
                "size_bytes": entry["bytes"],
            }
        )
    out: dict[str, Any] = {
        "schema_version": "1.0",
        "created_at": doc["created"],
        "sources": sources,
    }
    if "prefs" in doc:
        out["ui"] = doc["prefs"]
    return out
