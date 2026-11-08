"""Replay a saved run and compare it with its results (#29).

The job copies the run's ``analysis.R`` and ``input/analysis.csv`` (file contents only,
so the copy is writable even when the run is read-only) into the job's own work folder
and runs ``Rscript --vanilla analysis.R`` there with the pinned R and opnca-r.
``analysis.R`` checks the input's MD5 and writes ``replay/``; nothing is written into the
run itself while R runs.

The job's review compares ``replay/*.csv`` with the run's ``results/*.csv``
(``replay_compare``) and writes ``replay.json`` (``replay-v1``) plus the replayed tables
into the job's staging folder, which the job manager moves to
``runs/<id>/logs/replay-<n>/``. The job fails only when R fails; a difference is a
finished replay with ``status: failed`` and a readable list of differences, which stays
with the run. ``sync`` copies the newest finished replay into the manifest's ``replay``;
completion (sealing) needs ``passed``, and only completed runs can be published.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

from opncafyr.adapters.opnca import export
from opncafyr.core import runs as runs_core
from opncafyr.core.jobmanager import (
    JobContext,
    JobManager,
    JobPrepareError,
    JobRequest,
    ReviewResult,
)
from opncafyr.core.manifest import atomic_write_json
from opncafyr.core.rscript import rscript_command
from opncafyr.core.runtime import RuntimeUnavailable
from opncafyr.workflows.nca.replay_compare import (
    DEFAULT_RTOL,
    compare_folders,
    describe,
    not_reproduced,
)

DEFAULT_TIMEOUT_S = 1800.0
REPORT = "replay.json"
_FOLDER = re.compile(r"^replay-(\d+)$")


def replay_folders(run: Path) -> list[tuple[int, Path]]:
    logs = run / "logs"
    found = []
    for path in logs.iterdir() if logs.is_dir() else []:
        match = _FOLDER.match(path.name)
        if match and path.is_dir():
            found.append((int(match.group(1)), path))
    return sorted(found)


def _next_output(run: Path) -> str:
    logs = run / "logs"
    taken = {
        int(m.group(1))
        for p in (logs.iterdir() if logs.is_dir() else [])
        if (m := re.match(r"^replay-(\d+)", p.name))
    }
    n = max(taken, default=0) + 1
    return f"{runs_core.RUNS_DIR}/{run.name}/logs/replay-{n}"


def _not_reproduced(run: Path) -> dict[str, str]:
    """The tables opnca-r's ``artifacts.json`` says ``analysis.R`` doesn't replay.

    The save was checked against ``COMPLETE`` when it was accepted, so the listing is read
    as is; if it can't be read, every table is compared.
    """
    try:
        listing = json.loads((run / export.ARTIFACTS).read_text("utf-8"))
    except (OSError, ValueError):
        return {}
    return not_reproduced(listing) if isinstance(listing, dict) else {}


def start(
    jobs: JobManager, run: Path, *, rtol: float = DEFAULT_RTOL, timeout_s: float = DEFAULT_TIMEOUT_S
) -> str:
    manifest = runs_core.read_manifest(run)
    if manifest["state"] != "reviewing":
        raise runs_core.RunError(
            "bad_state", f"Only a saved run in review is replayed; this one is {manifest['state']}."
        )
    return jobs.submit(_request(run, manifest, rtol, timeout_s, _next_output(run)))


def _request(
    run: Path, manifest: dict[str, Any], rtol: float, timeout_s: float, output: str
) -> JobRequest:
    def build(ctx: JobContext):
        (ctx.cwd / "input").mkdir(parents=True, exist_ok=True)
        shutil.copyfile(run / "analysis.R", ctx.cwd / "analysis.R")
        shutil.copyfile(run / "input" / "analysis.csv", ctx.cwd / "input" / "analysis.csv")
        try:
            return rscript_command(ctx.runtime, ctx.cwd / "analysis.R")
        except RuntimeUnavailable as exc:
            raise JobPrepareError(f"R is not available: {exc}") from None

    def review(ctx: JobContext) -> ReviewResult:
        assert ctx.staging is not None  # noqa: S101 - output is always set
        replayed = ctx.cwd / "replay"
        result = compare_folders(run / "results", replayed, rtol, _not_reproduced(run))
        (ctx.staging / "replay").mkdir()
        for table in sorted(replayed.glob("*.csv")) if replayed.is_dir() else []:
            shutil.copyfile(table, ctx.staging / "replay" / table.name)
        report = {
            "schema_version": "1.0",
            "run_id": run.name,
            "at": runs_core.now(),
            "rtol": rtol,
            "status": result["status"],
            "problem": result["problem"],
            "tables": result["tables"],
            "not_reproduced": result["not_reproduced"],
            "differences": describe(result),
            "script_sha256": manifest["script"]["sha256"],
            "input_sha256": manifest["analysis_input"]["sha256"],
            "tools": {k: v for k, v in manifest["tools"].items() if k in ("opnca-r", "R")},
        }
        atomic_write_json(ctx.staging / REPORT, report, "replay-v1")
        return ReviewResult(True)

    def explain(code: int) -> str:
        return (
            f"analysis.R stopped with exit code {code}: the saved analysis did not "
            "replay. The job's error log has R's message."
        )

    return JobRequest(
        kind="nca.replay",
        label=f"Replay run {run.name}",
        build=build,
        timeout_s=timeout_s,
        output=output,
        review=review,
        explain=explain,
    )


def latest(run: Path) -> tuple[Path, dict[str, Any]] | None:
    for _, folder in reversed(replay_folders(run)):
        try:
            return folder, json.loads((folder / REPORT).read_text("utf-8"))
        except (OSError, ValueError):
            continue
    return None


def sync(run: Path) -> dict[str, Any]:
    """Record the newest finished replay in the manifest of a run in review."""
    manifest = runs_core.read_manifest(run)
    if manifest["state"] != "reviewing":
        return manifest
    found = latest(run)
    if found is None:
        return manifest
    folder, report = found
    rel = (folder / REPORT).relative_to(run).as_posix()
    entry = {
        "status": report["status"],
        "rtol": report["rtol"],
        "path": rel,
        "sha256": runs_core.sha256_file(folder / REPORT),
        "at": report["at"],
    }
    if manifest.get("replay") == entry:
        return manifest
    return runs_core.set_state(run, "reviewing", replay=entry)
