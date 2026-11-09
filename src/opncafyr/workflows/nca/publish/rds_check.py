"""Preflight for formatted tables (``.rds``) in a report (#62).

opnca-r before 0.3.2 saved each formatted table as a flextable that carries its own caption
(opnca-r#59 removed it in 0.3.2, but a run's files are immutable, so older runs keep it). In the
cloud walkthrough a report that included one kept its ``{rpfy}`` placeholder unfilled, and
the render stopped. The likely reason is that reportifyr 0.4.0 matches a table to its
placeholder by the paragraph just before the table, and a caption paragraph sits there.
That is not confirmed (reportifyr is external), so this check only reports what it can see
in the file and names the observed effect. A report with such a table is not rendered; the
CSV version of the same table, or the deck, can be used instead. The published file is
never changed: its hash is recorded in the publication.

A fixed R program reads each ``.rds`` copy in the project (they were matched to their
publication's hashes when copied) and writes only a status per file.
"""

from __future__ import annotations

import json
from importlib import resources
from pathlib import Path
from typing import Any

from opncafyr.core.proctree import DEVNULL, TimeoutExpired, child_env, spawn
from opncafyr.core.rscript import rscript_command
from opncafyr.core.runtime import Runtime

PROGRAM = "report_rds_check.R"
TIMEOUT_S = 120.0

CAPTIONED = (
    "This formatted table has a caption of its own (opnca-r before 0.3.2), which left its "
    "placeholder unfilled (#62). Use the CSV version in the report, use this one in a deck, "
    "or make a new run."
)
NOT_FLEXTABLE = (
    "This .rds file is not a formatted table (a flextable), so the report can't place it."
)
UNREADABLE = "R could not read this .rds file."


def rds_paths(listed: list[str]) -> list[str]:
    return [p for p in listed if p.lower().endswith(".rds")]


def findings_from(result: dict[str, Any]) -> list[dict[str, Any]]:
    """Findings for one program result (``{"files": [{"path", "status"}]}``)."""
    out = []
    for item in result.get("files", []):
        status = item.get("status")
        message, check = {
            "captioned": (CAPTIONED, "rds-captioned"),
            "not_flextable": (NOT_FLEXTABLE, "rds-not-flextable"),
            "unreadable": (UNREADABLE, "rds-unreadable"),
        }.get(status, (None, None))
        if message is not None:
            out.append(
                {"check": check, "severity": "error", "path": item["path"], "message": message}
            )
    return out


def rds_findings(
    runtime: Runtime, project: Path, listed: list[str], tmpdir: Path, timeout_s: float = TIMEOUT_S
) -> list[dict[str, Any]]:
    """Check the ``.rds`` files among ``listed`` (project-relative published paths)."""
    paths = rds_paths(listed)
    if not paths:
        return []
    work = tmpdir / "rds-check"
    work.mkdir(parents=True, exist_ok=True)
    script = work / PROGRAM
    script.write_text(
        resources.files("opncafyr.r").joinpath(PROGRAM).read_text("utf-8"),
        "utf-8",
        newline="\n",
    )
    names = work / "files.txt"
    names.write_text("\n".join(paths) + "\n", "utf-8", newline="\n")
    out = work / "result.json"
    spec = rscript_command(
        runtime,
        script,
        env={
            "RDS_CHECK_BASE": str(project),
            "RDS_CHECK_LIST": str(names),
            "RDS_CHECK_OUT": str(out),
        },
    )
    env = child_env(spec.env, spec.path_dirs)
    reason = None
    try:
        proc = spawn(spec.argv, env=env, cwd=work, stdout=DEVNULL, stderr=DEVNULL)
    except (OSError, ValueError) as exc:
        proc, reason = None, f"R could not start ({type(exc).__name__})"
    if proc is not None:
        try:
            code = proc.wait(timeout_s)
            if code != 0:
                reason = f"R exited with code {code}"
        except TimeoutExpired:
            reason = f"R did not finish within {timeout_s:g} seconds"
        finally:
            proc.kill_tree()
    if reason is None:
        try:
            return findings_from(json.loads(out.read_text("utf-8")))
        except (OSError, ValueError):
            reason = "R wrote no readable result"
    # Not inspected: say so, but don't block; a render that can't place a table stops anyway.
    return [
        {
            "check": "rds-not-inspected",
            "severity": "warning",
            "path": None,
            "message": f"The formatted tables could not be inspected ({reason}).",
        }
    ]
