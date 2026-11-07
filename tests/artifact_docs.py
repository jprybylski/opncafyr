"""Valid run manifests and publications for tests, written where a workspace keeps them."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

SHA = "0" * 64


def run_manifest(run_id: str = "20261103T142512Z-7c4e", **overrides: Any) -> dict[str, Any]:
    doc = {
        "schema_version": "1.0",
        "run_id": run_id,
        "created_at": "2026-11-03T14:25:12Z",
        "state": "completed",
        "source": {"path": "source-data/pk.csv", "sha256": SHA, "size_bytes": 10},
        "analysis_input": {"path": "input/analysis.csv", "sha256": SHA, "rows": 128},
        "analyte": {"column": "analyte", "value": "Example A"},
        "recipe": {"id": "pk-1", "sha256": SHA},
        "mapping": {"roles": {}},
        "units": {},
        "tools": {"opncafyr": "0.0.1", "opnca-r": "0.0.0", "R": "4.5.2"},
    }
    doc.update(overrides)
    if doc["state"] in ("reviewing", "completed"):
        doc.setdefault("script", {"path": "analysis.R", "sha256": SHA})
        doc.setdefault("record", {"path": "analysis-record.json", "sha256": SHA})
        doc.setdefault("export", {"saved_at": "2026-11-03T14:30:00Z", "artifacts_sha256": SHA})
        doc.setdefault("artifacts", [])
        doc.setdefault("nca_settings", {})
    if doc["state"] == "completed":
        doc.setdefault(
            "replay",
            {
                "status": "passed",
                "rtol": 1e-10,
                "path": "logs/replay.json",
                "sha256": SHA,
                "at": "2026-11-03T14:31:00Z",
            },
        )
        doc.setdefault("sealed", {"at": "2026-11-03T14:32:00Z", "files": []})
    if doc["state"] == "failed":
        doc.setdefault(
            "failure", {"reason": "save_failed", "message": "x", "at": "2026-11-03T14:32:00Z"}
        )
    return doc


def publication(publication_id: str = "pub-1", **overrides: Any) -> dict[str, Any]:
    doc = {
        "schema_version": "1.0",
        "publication_id": publication_id,
        "created_at": "2026-11-04T09:00:00Z",
        "items": [
            {
                "run_id": "20261103T142512Z-7c4e",
                "artifact_path": "artifacts/figure.png",
                "artifact_sha256": SHA,
                "published_path": "OUTPUTS/figures/figure.png",
            }
        ],
    }
    doc.update(overrides)
    return doc


def write_run(root: Path, doc: dict[str, Any] | None, run_id: str | None = None) -> Path:
    """Create ``.opncafyr/runs/<run_id>/``, with ``manifest.json`` unless ``doc`` is None."""
    name = run_id or (doc or {}).get("run_id") or "20261103T142512Z-dead"
    folder = root / ".opncafyr" / "runs" / name
    folder.mkdir(parents=True, exist_ok=True)
    if doc is not None:
        (folder / "manifest.json").write_text(json.dumps(doc), "utf-8")
    return folder


def write_publication(root: Path, doc: dict[str, Any], name: str | None = None) -> Path:
    folder = root / ".opncafyr" / "publications"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{name or doc['publication_id']}.json"
    path.write_text(json.dumps(doc), "utf-8")
    return path
