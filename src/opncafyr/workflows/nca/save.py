"""From opnca-r's save to a run ready for review (#28).

The user saves in opnca-r (its Export view's Save button posts the figures, so only the
page can trigger it). The launcher notices on disk: the Analysis page polls
``POST /api/nca/runs/{id}/save-status`` while a run is open, and nothing is read from a
browser download.

When ``COMPLETE`` appears the session is stopped first, so nothing else can be written,
then the folder is inspected (``adapters.opnca.export``). A complete save moves the run to
``reviewing`` with its script, record, artifacts (path, media type, bytes, SHA-256),
opnca-r's settings and effective settings, the tool versions and the exclusions. A save
that can't be complete moves it to ``failed``; the user makes a new run from it.

One rule is the launcher's own: the snapshot's exclusions are in ``NCA_EXCLUDED``. Once a
point is excluded inside opnca-r, its page analyses with ``OPNCA_EXCLUDED`` instead, which
opnca-r starts all FALSE, so the launcher's exclusions would be silently dropped. Such a
save fails (upstream issue recorded in docs/upstream.md).
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from opncafyr.adapters.opnca import export
from opncafyr.core import runs as runs_core
from opncafyr.workflows.nca.snapshot import SNAPSHOT_COLUMNS

# The run folder's own entries, next to opnca-r's.
OWN = ("input", "settings.json", "manifest.json", "logs", "artifacts", "replay")


def launcher_exclusions(folder: Path) -> dict[str, Any]:
    column = SNAPSHOT_COLUMNS["EXCLUDED"]
    reason_col = SNAPSHOT_COLUMNS["EXCLUSION_REASON"]
    with (folder / "input" / "analysis.csv").open(newline="", encoding="utf-8") as fh:
        rows = [r for r in csv.DictReader(fh) if r.get(column) == "TRUE"]
    return {"rows": len(rows), "reasons": sorted({r.get(reason_col, "") for r in rows})}


def check(folder: Path, session_alive: bool) -> export.ExportStatus:
    manifest = runs_core.read_manifest(folder)
    return export.inspect_export(
        folder,
        own=OWN,
        snapshot_sha256=manifest["analysis_input"]["sha256"],
        session_alive=session_alive,
    )


def accept(folder: Path, status: export.ExportStatus) -> dict[str, Any]:
    """Move a run with a complete save to ``reviewing``, or to ``failed`` with the reason."""
    manifest = runs_core.read_manifest(folder)
    record = status.record or {}
    ours = launcher_exclusions(folder)
    mapped = (record.get("mapping") or {}).get("excluded")
    if ours["rows"] and mapped != SNAPSHOT_COLUMNS["EXCLUDED"]:
        status.state = "failed"
        status.problems.append(
            "Points were excluded inside opnca-r, which replaced the launcher's "
            f"{ours['rows']} excluded rows with its own exclusions. Make a new run and "
            "exclude points with a recipe step instead."
        )
    if status.state != "complete":
        return runs_core.set_state(
            folder,
            "failed",
            failure={
                "reason": "save_failed",
                "message": " ".join(status.problems),
                "at": runs_core.now(),
            },
        )
    session = record.get("exclusions") or {}
    observations = session.get("observations") or []
    if isinstance(observations, dict):  # a data frame written column-wise
        observations = [
            dict(zip(observations, v, strict=False))
            for v in zip(*observations.values(), strict=False)
        ]
    in_session = [o for o in observations if mapped != SNAPSHOT_COLUMNS["EXCLUDED"]]
    warnings = list(manifest.get("warnings", []))
    if in_session:
        warnings.append(
            f"session_exclusion_no_reason: {len(in_session)} points were excluded in opnca-r, "
            "which records no reason."
        )
    tools = dict(manifest["tools"])
    tools["opnca-r"] = str(record.get("opnca_version", ""))
    tools["R"] = str(record.get("r_version", ""))
    nca_settings = dict(manifest.get("nca_settings") or {})
    nca_settings.update(
        {
            "settings": record.get("settings"),
            "effective_settings": record.get("effective_settings"),
            "terminal_rules": record.get("terminal_rules"),
            "mapping": record.get("mapping"),
        }
    )
    by_path = {a["path"]: a for a in status.artifacts}
    return runs_core.set_state(
        folder,
        "reviewing",
        script={"path": export.SCRIPT, "sha256": by_path[export.SCRIPT]["sha256"]},
        record={"path": export.RECORD, "sha256": by_path[export.RECORD]["sha256"]},
        export={
            "saved_at": str(status.complete.get("saved_at", "")),
            "artifacts_sha256": status.complete["artifacts_sha256"],
        },
        artifacts=[
            {
                "path": a["path"],
                "sha256": a["sha256"],
                "kind": a["kind"],
                "media_type": a.get("media_type", "application/octet-stream"),
                "bytes": a["bytes"],
            }
            for a in status.artifacts
        ],
        nca_settings=nca_settings,
        exclusions={"launcher": ours, "session": in_session},
        tools=tools,
        warnings=warnings,
    )
