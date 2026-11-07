"""Run a cleaning recipe as a job (#22).

Each run of a recipe on a source writes one immutable folder,
``.opncafyr/cleaned/<recipe-id>-<key>/``:

- ``clean.R``: the generated script (runs standalone, see ``clean_compile``);
- ``meta.json``: recipe, source and script hashes, and the preview sample size;
- ``cleaned.csv``, ``rejected.csv`` (line, step, reason) and ``steps.json`` (per-step
  counts, changes, issues and duplicate groups), written by R.

``key`` covers the recipe hash, the source SHA-256, the sample size and the script hash,
so a changed recipe, source or launcher never reuses an old result. A preview is the
same script on the first ``PREVIEW_ROWS`` rows. A job's ``review`` checks the files, that
``rows_in == rows_out + rows_rejected`` for every step, and that the source is unchanged;
only then does the job manager move the folder into place.
"""

from __future__ import annotations

import csv
import hashlib
import json
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from opncafyr.core.datasets import fingerprint
from opncafyr.core.jobmanager import (
    JobContext,
    JobManager,
    JobPrepareError,
    JobRequest,
    ReviewResult,
)
from opncafyr.core.jobs import TERMINAL_STATES, JobState
from opncafyr.core.manifest import atomic_write_json
from opncafyr.core.rscript import rscript_command
from opncafyr.core.runtime import RuntimeUnavailable
from opncafyr.core.workspace import WorkspaceAccessError, resolve_in_root
from opncafyr.workflows.nca.clean_compile import (
    ENV_INPUT,
    ENV_OUTPUT,
    ENV_SAMPLE,
    compile_recipe,
    read_header,
)
from opncafyr.workflows.nca.importers.csv import sniff_delimiter
from opncafyr.workflows.nca.recipe import check as check_recipe
from opncafyr.workflows.nca.recipe import steps_hash

CLEANED_DIR = ".opncafyr/cleaned"
PREVIEW_ROWS = 200
CLEAN_TIMEOUT_S = 1800.0
SNIFF_BYTES = 64 * 1024
OUTPUT_FILES = ("clean.R", "meta.json", "cleaned.csv", "rejected.csv", "steps.json")


class CleaningError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class CleanTarget:
    recipe: dict[str, Any]
    steps_sha256: str
    source: dict[str, Any]
    source_path: Path
    delimiter: str
    header: list[str]
    columns: list[str]
    script: str
    sample_rows: int
    key: str

    @property
    def output(self) -> str:
        return f"{CLEANED_DIR}/{self.recipe['id']}-{self.key}"

    def meta(self) -> dict[str, Any]:
        return {
            "recipe_id": self.recipe["id"],
            "steps_sha256": self.steps_sha256,
            "source_id": self.source["id"],
            "source_path": self.source["path"],
            "source_sha256": self.source["sha256"],
            "sample_rows": self.sample_rows,
            "script_sha256": _sha(self.script),
            "key": self.key,
        }


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def find_source(document: dict[str, Any], source_id: str) -> dict[str, Any]:
    for entry in document.get("sources", []):
        if entry["id"] == source_id:
            return entry
    raise CleaningError("source_not_found", "The recipe's source is not imported in this study.")


def sniff(path: Path) -> str:
    with path.open("rb") as fh:
        raw = fh.read(SNIFF_BYTES)
    text = raw.decode("utf-8", errors="replace").lstrip("﻿")
    return sniff_delimiter(text, len(raw) < SNIFF_BYTES)


def plan(
    root: Path, document: dict[str, Any], recipe: dict[str, Any], sample_rows: int = 0
) -> CleanTarget:
    """Everything about a cleaning run that doesn't need R. Raises for an invalid recipe."""
    source = find_source(document, recipe["source_id"])
    try:
        path = resolve_in_root(root, source["path"])
    except WorkspaceAccessError:
        raise CleaningError("source_not_found", "The source path is outside the study.") from None
    if not path.is_file():
        raise CleaningError("source_not_found", f"{source['path']} is missing.")
    delimiter = sniff(path)
    header = read_header(path, delimiter)
    columns = check_recipe(recipe, header).columns
    script = compile_recipe(recipe, source_rel=source["path"], delimiter=delimiter)
    digest = steps_hash(recipe)
    key_text = "\n".join([digest, source["sha256"], str(sample_rows), _sha(script)])
    return CleanTarget(
        recipe=recipe,
        steps_sha256=digest,
        source=source,
        source_path=path,
        delimiter=delimiter,
        header=header,
        columns=columns,
        script=script,
        sample_rows=sample_rows,
        key=_sha(key_text)[:12],
    )


def check_output(folder: Path, target: CleanTarget) -> str | None:
    """Why ``folder`` is not a complete, consistent result for ``target`` (None if it is)."""
    for name in OUTPUT_FILES:
        if not (folder / name).is_file():
            return f"{name} is missing."
    try:
        meta = json.loads((folder / "meta.json").read_text("utf-8"))
        steps = json.loads((folder / "steps.json").read_text("utf-8"))
    except (OSError, ValueError):
        return "The result files can't be read."
    if meta != target.meta():
        return "The result was made from a different recipe, source or script."
    if steps.get("steps_sha256") != target.steps_sha256:
        return "steps.json names a different recipe."
    report = steps.get("steps") or []
    if len(report) != len(target.recipe["steps"]):
        return "steps.json does not cover every step."
    rows = steps["source_rows"]
    rejected = 0
    for entry in report:
        if entry["rows_in"] != rows:
            return f"Step {entry['index']} starts with a different row count."
        if entry["rows_in"] != entry["rows_out"] + entry["rows_rejected"]:
            return f"Step {entry['index']}: rows in != rows out + rows rejected."
        rows = entry["rows_out"]
        rejected += entry["rows_rejected"]
    if rows != steps["rows_out"] or rejected != steps["rows_rejected"]:
        return "The totals in steps.json don't add up."
    if _count_rows(folder / "cleaned.csv") != rows:
        return "cleaned.csv has a different number of rows."
    if _count_rows(folder / "rejected.csv") != rejected:
        return "rejected.csv has a different number of rows."
    return None


def _count_rows(path: Path) -> int:
    with path.open(newline="", encoding="utf-8") as fh:
        return max(sum(1 for _ in csv.reader(fh)) - 1, 0)


def read_cleaned(folder: Path) -> tuple[list[str], list[list[str]]]:
    """The header and every row of ``cleaned.csv``, as text."""
    with (folder / "cleaned.csv").open(newline="", encoding="utf-8") as fh:
        reader = csv.reader(fh)
        header = next(reader, [])
        return header, list(reader)


def load_result(folder: Path, head_rows: int = 20) -> dict[str, Any]:
    steps = json.loads((folder / "steps.json").read_text("utf-8"))
    with (folder / "cleaned.csv").open(newline="", encoding="utf-8") as fh:
        reader = csv.reader(fh)
        columns = next(reader, [])
        head = [row for _, row in zip(range(head_rows), reader, strict=False)]
    with (folder / "rejected.csv").open(newline="", encoding="utf-8") as fh:
        rejected = list(csv.DictReader(fh))
    return {"summary": steps, "columns": columns, "head": head, "rejected": rejected}


def _stderr_error(job_dir: Path) -> str | None:
    """The R error line of a failed run (messages name lines and columns, never values)."""
    try:
        lines = (job_dir / "stderr.log").read_text("utf-8", errors="replace").splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        if line.startswith("Error"):
            return line.split(":", 1)[-1].strip()[:400] or None
    return None


class CleaningService:
    """Submits cleaning jobs, reusing a valid result and never running one twice."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._inflight: dict[str, str] = {}

    def start(
        self,
        jobs: JobManager,
        root: Path,
        document: dict[str, Any],
        recipe: dict[str, Any],
        *,
        sample_rows: int = 0,
    ) -> dict[str, Any]:
        target = plan(root, document, recipe, sample_rows)
        folder = resolve_in_root(root, target.output)
        with self._lock:
            job_id = self._inflight.get(target.output)
            if job_id is not None:
                try:
                    state = JobState(jobs.get(job_id)["state"])
                except KeyError:
                    state = JobState.FAILED
                if state not in TERMINAL_STATES:
                    return {"job_id": job_id, "output": target.output, "cached": False}
                del self._inflight[target.output]
            if folder.is_dir():
                if check_output(folder, target) is None:
                    return {"job_id": None, "output": target.output, "cached": True}
                folder.rename(folder.with_name(f"{folder.name}.invalid-{int(time.time())}"))
            size, sha = fingerprint(target.source_path)
            if sha != target.source["sha256"] or size != target.source["size_bytes"]:
                raise CleaningError(
                    "source_changed",
                    f"{target.source['path']} changed since it was imported. Import it again.",
                )
            job_id = jobs.submit(_request(target))
            self._inflight[target.output] = job_id
            return {"job_id": job_id, "output": target.output, "cached": False}


def _request(target: CleanTarget) -> JobRequest:
    seen: dict[str, Path] = {}
    what = "Preview cleaning" if target.sample_rows else "Clean"

    def build(ctx: JobContext):
        assert ctx.staging is not None  # noqa: S101 - output is always set
        seen["job_dir"] = ctx.job_dir
        script = ctx.staging / "clean.R"
        script.write_text(target.script, "utf-8", newline="\n")
        atomic_write_json(ctx.staging / "meta.json", target.meta())
        try:
            return rscript_command(
                ctx.runtime,
                script,
                env={
                    ENV_INPUT: str(target.source_path),
                    ENV_OUTPUT: str(ctx.staging),
                    ENV_SAMPLE: str(target.sample_rows),
                },
            )
        except RuntimeUnavailable as exc:
            raise JobPrepareError(f"R is not available: {exc}") from None

    def review(ctx: JobContext) -> ReviewResult:
        assert ctx.staging is not None  # noqa: S101
        problem = check_output(ctx.staging, target)
        if problem:
            return ReviewResult(False, problem)
        _, sha = fingerprint(target.source_path)
        if sha != target.source["sha256"]:
            return ReviewResult(False, "The source file changed while it was being cleaned.")
        return ReviewResult(True)

    def explain(code: int) -> str:
        detail = _stderr_error(seen["job_dir"]) if "job_dir" in seen else None
        if detail:
            return f"Cleaning stopped: {detail}"
        return f"The cleaning script failed (exit code {code}). The job log has details."

    return JobRequest(
        kind="nca.clean",
        label=f"{what} {target.source['path']}",
        build=build,
        timeout_s=CLEAN_TIMEOUT_S,
        output=target.output,
        review=review,
        explain=explain,
    )
