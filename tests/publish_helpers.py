"""Completed, sealed runs with an opnca-r 0.3.1-shaped save, without R (#31-#33)."""

from __future__ import annotations

import json
import stat
import struct
import time
import zlib
from contextlib import contextmanager
from pathlib import Path

from fastapi.testclient import TestClient

from artifact_docs import SHA, run_manifest
from opnca_listing import describe
from opncafyr.core import runs as R
from opncafyr.core.app import create_app
from opncafyr.core.jobmanager import JobManager
from opncafyr.core.runtime import Runtime
from opncafyr.core.security import SESSION_HEADER
from opncafyr.core.workspace import RecentWorkspaces


def png(note: str, size: int = 8) -> bytes:
    """A valid grey PNG (``size`` px square) carrying ``note`` in a text chunk."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    rows = b"".join(b"\x00" + b"\x80" * size for _ in range(size))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 0, 0, 0, 0))
        + chunk(b"tEXt", b"Comment\x00" + note.encode())
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )


MEDIA = {
    ".csv": "text/csv",
    ".png": "image/png",
    ".R": "text/x-r",
    ".json": "application/json",
    ".rds": "application/octet-stream",
}
KIND = {".csv": "table", ".png": "figure", ".R": "script", ".json": "record", ".rds": "other"}


def writable(root: Path) -> None:
    """Undo sealing's read-only bits so pytest can clean up."""
    for path in [root, *root.rglob("*")]:
        if not path.is_symlink():
            path.chmod(path.stat().st_mode | stat.S_IWUSR)


MEAN_PROFILES = {
    "file": "results/opnca-mean-profiles.csv",
    "estimand": "observed_only",
    "time_basis": "nominal",
    "time_column": "NFRLT",
    "statistic": "arithmetic",
    "error_measure": "sd",
    "grouping_view": "none",
    "grouping": "None",
}


# What opnca-r 0.3.1 adds to a described save in these tests: formatted tables, a table
# the replay doesn't recompute (with its formatted copy), and a figure the launcher has no
# rule for.
DESCRIBED_EXTRA = (
    "results/tables/opnca-summary.rds",
    "results/tables/opnca-parameters-wide.rds",
    "results/bioequivalence-summary.csv",
    "results/tables/bioequivalence-summary.rds",
    "results/figures/fig-fim-cohort.png",
)


def saved_files(
    analyte: str,
    run_id: str,
    mean: bool = True,
    described: bool = False,
    empty_cell: bool = False,
) -> dict[str, bytes]:
    tag = f"{analyte} {run_id}"
    files = {
        "analysis.R": b'df <- read.csv("input/analysis.csv")\n',
        "analysis-record.json": b'{"data": {}}\n',
        "results/opnca-summary.csv": (
            f"metric,mean,note\ncmax,1.5,{tag}\n" + (f"auc_inf,,{tag}\n" if empty_cell else "")
        ).encode(),
        "results/opnca-parameters-wide.csv": f"USUBJID,cmax,note\nS-001,1.5,{tag}\n".encode(),
        "results/opnca-parameters-long.csv": b"profile_id,metric,value\nS-001,cmax,1\n",
        "results/figures/profiles-linear.png": png(f"linear {tag}"),
        "results/figures/profiles-log.png": png(f"log {tag}"),
        "results/figures/boxplot-cmax.png": png(f"box {tag}"),
    }
    if mean:
        files |= {
            "results/opnca-mean-profiles.csv": f"group,time,mean,note\nall,1,1.5,{tag}\n".encode(),
            "results/figures/mean-linear.png": png(f"mean linear {tag}"),
            "results/figures/mean-log.png": png(f"mean log {tag}"),
        }
    if described:
        for rel in DESCRIBED_EXTRA:
            files[rel] = png(rel) if rel.endswith(".png") else f"{rel} {tag}\n".encode()
    return files


def completed_run(
    root: Path,
    analyte: str = "Drug A",
    *,
    label: str | None = None,
    state: str = "completed",
    replay: str = "passed",
    mean: bool = True,
    described: bool = False,
    empty_cell: bool = False,
) -> tuple[str, Path]:
    """A run of ``analyte`` moved to ``state`` (reviewing or completed) the real way.

    ``described``: the artifacts carry opnca-r 0.3.1's ``artifacts.json`` schema-2 fields,
    as ``save.accept`` keeps them (#59); otherwise they are an older save's.
    ``empty_cell``: the summary table has a parameter that couldn't be estimated, written as
    an empty field, as opnca-r does (#62; deckifyr#96)."""
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
    for rel, data in saved_files(analyte, run_id, mean, described, empty_cell).items():
        path = folder / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        suffix = Path(rel).suffix
        art = {
            "path": rel,
            "sha256": R.sha256_file(path),
            "kind": KIND[suffix],
            "media_type": MEDIA[suffix],
            "bytes": len(data),
        }
        artifacts.append(art | describe(rel) if described else art)
    R.set_state(
        folder,
        "reviewing",
        script={"path": "analysis.R", "sha256": R.sha256_file(folder / "analysis.R")},
        record={"path": "analysis-record.json", "sha256": SHA},
        export={"saved_at": "2026-11-03T14:30:00Z", "artifacts_sha256": SHA},
        artifacts=artifacts,
        nca_settings={"mean_profiles": MEAN_PROFILES} if mean else {},
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


PRESELECTED = ("summary", "listing", "profiles-linear", "profiles-log", "mean-linear", "mean-log")
# What most tests publish: two tables and two figures.
BASIC = PRESELECTED[:4]


def picks(run_id: str, artifacts=BASIC) -> list[dict]:
    return [{"run_id": run_id, "artifact": a} for a in artifacts]


@contextmanager
def publish_client(security, tmp_path: Path, study: Path, runtime: Runtime | None = None, **kw):
    """A TestClient with the session set and ``study`` opened, with jobs on ``runtime``."""
    jobs = JobManager(runtime=runtime or Runtime(), poll_interval=0.05)
    app = create_app(
        security,
        jobs=jobs,
        picker=lambda prompt, start: study,
        recent=RecentWorkspaces(tmp_path / "state" / "recent.json"),
        runtime=runtime or Runtime(),
        **kw,
    )
    with TestClient(
        app,
        base_url=f"http://127.0.0.1:{security.port}",
        cookies={security.cookie_name: security.secret},
    ) as client:
        client.headers[SESSION_HEADER] = security.secret
        folder = client.post("/api/workspace/pick").json()["folder"]
        r = client.post("/api/workspace/init", json={"path": folder["path"]})
        if r.status_code != 200:  # already a workspace: open it
            client.post("/api/workspace/open", json={"path": folder["path"]}).raise_for_status()
        client.study = study
        yield client
    jobs.shutdown()


def wait_job(client, job_id: str, timeout: float = 300.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()["job"]
        if job["state"] in ("completed", "failed", "cancelled"):
            return job
        time.sleep(0.1)
    raise TimeoutError(job_id)
