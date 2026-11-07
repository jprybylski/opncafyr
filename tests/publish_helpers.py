"""Completed, sealed runs with an opnca-r 0.2.8-shaped save, without R (#31-#33)."""

from __future__ import annotations

import json
import stat
from pathlib import Path

from artifact_docs import SHA, run_manifest
from opncafyr.core import runs as R

PNG = b"\x89PNG\r\n\x1a\n"
MEDIA = {".csv": "text/csv", ".png": "image/png", ".R": "text/x-r", ".json": "application/json"}
KIND = {".csv": "table", ".png": "figure", ".R": "script", ".json": "record"}


def writable(root: Path) -> None:
    """Undo sealing's read-only bits so pytest can clean up."""
    for path in [root, *root.rglob("*")]:
        if not path.is_symlink():
            path.chmod(path.stat().st_mode | stat.S_IWUSR)


def saved_files(analyte: str, run_id: str) -> dict[str, bytes]:
    tag = f"{analyte} {run_id}".encode()
    return {
        "analysis.R": b'df <- read.csv("input/analysis.csv")\n',
        "analysis-record.json": b'{"data": {}}\n',
        "results/opnca-summary.csv": b"metric,mean\ncmax," + tag + b"\n",
        "results/opnca-parameters-wide.csv": b"USUBJID,cmax\nS-001," + tag + b"\n",
        "results/opnca-parameters-long.csv": b"profile_id,metric,value\nS-001,cmax,1\n",
        "results/figures/profiles-linear.png": PNG + b"linear " + tag,
        "results/figures/profiles-log.png": PNG + b"log " + tag,
        "results/figures/boxplot-cmax.png": PNG + b"box " + tag,
    }


def completed_run(
    root: Path,
    analyte: str = "Drug A",
    *,
    label: str | None = None,
    state: str = "completed",
    replay: str = "passed",
) -> tuple[str, Path]:
    """A run of ``analyte`` moved to ``state`` (reviewing or completed) the real way."""
    (root / ".opncafyr").mkdir(parents=True, exist_ok=True)
    run_id, staging = R.stage_run(root)
    (staging / "input").mkdir()
    (staging / "input" / "analysis.csv").write_text("USUBJID,TIME,CONC\nS-001,0,1\n", "utf-8")
    (staging / "settings.json").write_text("{}", "utf-8")
    chosen = {"column": "PARAM", "value": analyte}
    if label:
        chosen["label"] = label
    doc = run_manifest(run_id, state="preparing", analyte=chosen)
    doc["analysis_input"]["sha256"] = R.sha256_file(staging / "input" / "analysis.csv")
    R.write_manifest(staging, doc)
    folder = R.publish_run(staging, run_id)
    R.set_state(folder, "running")
    artifacts = []
    for rel, data in saved_files(analyte, run_id).items():
        path = folder / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        suffix = Path(rel).suffix
        artifacts.append(
            {
                "path": rel,
                "sha256": R.sha256_file(path),
                "kind": KIND[suffix],
                "media_type": MEDIA[suffix],
                "bytes": len(data),
            }
        )
    R.set_state(
        folder,
        "reviewing",
        script={"path": "analysis.R", "sha256": R.sha256_file(folder / "analysis.R")},
        record={"path": "analysis-record.json", "sha256": SHA},
        export={"saved_at": "2026-11-03T14:30:00Z", "artifacts_sha256": SHA},
        artifacts=artifacts,
        nca_settings={},
    )
    if state == "reviewing":
        return run_id, folder
    report = folder / "logs" / "replay-1" / "replay.json"
    report.parent.mkdir(parents=True)
    report.write_text(json.dumps({"status": replay}), "utf-8")
    R.seal_run(
        folder,
        replay={
            "status": replay,
            "rtol": 1e-10,
            "path": "logs/replay-1/replay.json",
            "sha256": R.sha256_file(report),
            "at": "2026-11-03T14:31:00Z",
        },
    )
    return run_id, folder


PRESELECTED = ("summary", "listing", "profiles-linear", "profiles-log")


def picks(run_id: str, artifacts=PRESELECTED) -> list[dict]:
    return [{"run_id": run_id, "artifact": a} for a in artifacts]
