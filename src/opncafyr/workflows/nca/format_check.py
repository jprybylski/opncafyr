"""opnca-r's ``check_data_format()`` on one analyte selection, as a job (#24).

Its issues are merged into the launcher's findings as info (``opnca_format_check``): the
launcher's own checks decide what blocks. The result is kept in
``.opncafyr/checks/<key>/format.json``, keyed on the cleaned data, the selection and the
program, so it runs once per selection.
"""

from __future__ import annotations

import hashlib
import json
import threading
from importlib import resources
from pathlib import Path
from typing import Any

from opncafyr.core.jobmanager import (
    JobContext,
    JobManager,
    JobPrepareError,
    JobRequest,
    ReviewResult,
)
from opncafyr.core.jobs import TERMINAL_STATES, JobState
from opncafyr.core.rscript import rscript_command
from opncafyr.core.runtime import RuntimeUnavailable
from opncafyr.core.workspace import resolve_in_root
from opncafyr.workflows.nca import mapping as M
from opncafyr.workflows.nca.checks import LEVELS, Selection

CHECKS_DIR = ".opncafyr/checks"
TIMEOUT_S = 300.0


def _program() -> str:
    return resources.files("opncafyr.r").joinpath("nca_format_check.R").read_text("utf-8")


def output_for(
    cleaned: Path, mapping: dict[str, Any], selection: Selection, table: str = "cleaned.csv"
) -> str:
    # The prepared table of an event recipe (#58) is its own input; cleaned.csv keys stay.
    name = cleaned.name if table == "cleaned.csv" else f"{cleaned.name}/{table}"
    text = json.dumps(
        [
            name,
            M.role(mapping, "ANALYTE"),
            selection.analyte_value,
            M.role(mapping, "SPECIMEN"),
            selection.specimen_value,
            hashlib.sha256(_program().encode()).hexdigest(),
        ],
    )
    return f"{CHECKS_DIR}/{hashlib.sha256(text.encode()).hexdigest()[:16]}"


def read(folder: Path, selection: Selection) -> list[dict[str, Any]] | None:
    try:
        report = json.loads((folder / "format.json").read_text("utf-8"))
    except (OSError, ValueError):
        return None
    return [
        {
            "check": "opnca_format_check",
            "level": LEVELS["opnca_format_check"],
            "analyte": selection.analyte_value,
            "scope": "analyte",
            "subject": None,
            "lines": [],
            "message": f"opnca-r {report.get('opnca_version', '')}: {issue}",
            "fix_hint": "From opnca-r's own data check; the launcher's checks decide what blocks.",
        }
        for issue in report.get("issues", [])
    ]


class FormatChecks:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._inflight: dict[str, str] = {}

    def start(
        self,
        jobs: JobManager,
        root: Path,
        cleaned: Path,
        mapping: dict[str, Any],
        selection: Selection,
        table: str = "cleaned.csv",
    ) -> dict[str, Any]:
        output = output_for(cleaned, mapping, selection, table)
        folder = resolve_in_root(root, output)
        with self._lock:
            if folder.is_dir():
                found = read(folder, selection)
                if found is not None:
                    return {"job_id": None, "findings": found}
            job_id = self._inflight.get(output)
            if job_id is not None:
                try:
                    if JobState(jobs.get(job_id)["state"]) not in TERMINAL_STATES:
                        return {"job_id": job_id, "findings": None}
                except KeyError:
                    pass
            if folder.exists():  # left over without a readable report
                return {"job_id": None, "findings": []}
            job_id = jobs.submit(_request(cleaned / table, mapping, selection, output))
            self._inflight[output] = job_id
            return {"job_id": job_id, "findings": None}


def _request(table: Path, mapping: dict[str, Any], selection: Selection, output: str):
    def build(ctx: JobContext):
        assert ctx.staging is not None  # noqa: S101
        script = ctx.staging / "nca_format_check.R"
        script.write_text(_program(), "utf-8", newline="\n")
        env = {
            "FORMAT_INPUT": str(table),
            "FORMAT_OUTPUT": str(ctx.staging),
            "FORMAT_ANALYTE_COLUMN": M.role(mapping, "ANALYTE") or "",
            "FORMAT_ANALYTE_VALUE": selection.analyte_value,
        }
        if M.role(mapping, "SPECIMEN") and selection.specimen_value is not None:
            env["FORMAT_SPECIMEN_COLUMN"] = M.role(mapping, "SPECIMEN") or ""
            env["FORMAT_SPECIMEN_VALUE"] = selection.specimen_value
        try:
            return rscript_command(ctx.runtime, script, env=env)
        except RuntimeUnavailable as exc:
            raise JobPrepareError(f"R is not available: {exc}") from None

    def review(ctx: JobContext) -> ReviewResult:
        assert ctx.staging is not None  # noqa: S101
        if read(ctx.staging, selection) is None:
            return ReviewResult(False, "opnca-r wrote no readable report.")
        return ReviewResult(True)

    return JobRequest(
        kind="nca.format-check",
        label="opnca-r data check",
        build=build,
        timeout_s=TIMEOUT_S,
        output=output,
        review=review,
    )
