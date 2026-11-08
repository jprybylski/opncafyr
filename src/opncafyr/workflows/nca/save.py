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

One rule is the launcher's own: the snapshot's exclusions are in ``NCA_EXCLUDED``, and the
record must still map ``excluded`` to it. Since opnca-r 0.2.9 (opnca-r#49) a point excluded
inside opnca-r is added to those rows (source ``mapping`` in the record) rather than
replacing them, and the record keeps the mapping. A save whose record maps another column
while the run has launcher exclusions would have dropped them, so it fails. The points
excluded inside opnca-r are the record's observations with any other source; they are kept
in the manifest with a warning, because opnca-r records no reason for them.

The other is the unit labels (#57). The preset declares the run's ``analysis_units`` and
opnca-r 0.3.1 locks them (opnca-r#56); the record must state exactly those labels, with
``units_source`` ``preset``. A save labelled otherwise fails rather than publishing results
labelled in units the numbers are not in.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from opncafyr.adapters.opnca import export
from opncafyr.core import runs as runs_core
from opncafyr.workflows.nca.snapshot import SNAPSHOT_COLUMNS

# opnca-r's source for rows the mapped ``excluded`` column flagged before the session.
MAPPING_SOURCE = "mapping"

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


def unit_problem(manifest: dict[str, Any], record: dict[str, Any]) -> str | None:
    """Why the record's unit labels are not the run's, or None when they are."""
    expected = manifest.get("analysis_units")
    effective = record.get("effective_settings") or {}
    recorded = effective.get("units")
    if not expected:
        return (
            "This run was prepared before its unit labels were passed to opnca-r. Make a new run."
        )
    if recorded != expected or effective.get("units_source") != "preset":
        want = ", ".join(f"{k} {expected.get(k)}" for k in ("concentration", "time", "dose"))
        return (
            f"opnca-r labelled the results in other units than the run's ({want}). "
            "Make a new run from this one."
        )
    return None


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
    problem = unit_problem(manifest, record)
    if status.state == "complete" and problem:
        status.state = "failed"
        status.problems.append(problem)
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
    in_session = [o for o in observations if o.get("source") != MAPPING_SOURCE]
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
    if isinstance(record.get("mean_profiles"), dict):
        nca_settings["mean_profiles"] = record["mean_profiles"]
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
