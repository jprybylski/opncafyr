"""opnca-r 0.3.1's ``artifacts.json`` schema 2 for fake saves, without R (#59).

``describe(path)`` gives the fields opnca-r's export registry (``R/app-export-registry.R``)
writes for a saved file, so tests can build listings and run manifests of the shape a real
0.3.1 save has. ``SCHEMA`` is the copy of opnca-r's shipped schema in ``fixtures/``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path, PurePosixPath
from typing import Any

SCHEMA_PATH = (
    Path(__file__).resolve().parent / "fixtures" / "opnca-r" / ("opnca-artifacts-v2.schema.json")
)
SCHEMA = json.loads(SCHEMA_PATH.read_text("utf-8"))

SUMMARY_NOTE = "Statistics use results with status ok; each estimand is summarised separately."
WIDE_NOTE = "Observed-only estimates; each column is named with its unit."
MEAN_NOTE = (
    "Observed-only summary of the prepared concentrations; the record states the time basis, "
    "statistic, error measure and BLQ treatment."
)
BE_REASON = "bioequivalence tables are not recomputed by analysis.R"
COLUMNS = [{"name": "metric", "label": "Metric", "unit": None}]

# id, title, analysis, identifies_subjects, settings, footnote; tables under results/.
_TABLES = {
    "opnca-summary": (
        "Summary of the parameters (selected grouping view)",
        "nca.summary",
        False,
        "/grouping",
        SUMMARY_NOTE,
    ),
    "opnca-parameters-wide": (
        "Parameters, one row per profile",
        "nca",
        True,
        "/effective_settings",
        WIDE_NOTE,
    ),
    "opnca-parameters-long": (
        "Parameters, one row per profile and parameter",
        "nca",
        True,
        "/effective_settings",
        None,
    ),
    "opnca-mean-profiles": (
        "Mean concentration-time profile",
        "mean_profiles",
        False,
        "/mean_profiles",
        MEAN_NOTE,
    ),
    "bioequivalence-summary": (
        "Bioequivalence: ratios and intervals",
        "bioequivalence",
        False,
        "/bioequivalence",
        None,
    ),
}
# Figures opnca-r's catalog knows (``fig-<id>``), by id: title, identifies_subjects, table.
_FIGURES = {
    "fim-cohort": ("Fisher information: cohort candidates", False, None),
    "results-overlay": ("Results: profile overlay", True, None),
}


def _replay(id_: str) -> dict[str, Any]:
    if id_.startswith("bioequivalence-"):
        return {"reproduced": False, "reason": BE_REASON}
    return {"reproduced": True, "file": f"replay/{id_}.csv"}


def _figure(name: str) -> dict[str, Any]:
    if m := re.fullmatch(r"profiles-(linear|log)", name):
        title, ids, table = f"Concentration-time profiles ({m.group(1)} axis)", True, None
    elif name.startswith("boxplot-"):
        title = f"Boxplot of {name.removeprefix('boxplot-')} by profile"
        ids, table = False, "opnca-parameters-wide"
    elif m := re.fullmatch(r"mean-(linear|log)", name):
        title = f"Mean concentration-time profile ({m.group(1)} axis)"
        ids, table = False, "opnca-mean-profiles"
    elif name.removeprefix("fig-") in _FIGURES:
        title, ids, table = _FIGURES[name.removeprefix("fig-")]
    else:  # a figure opnca-r's catalog doesn't know: the cautious reading
        title, ids, table = name.removeprefix("fig-").replace("-", " ").capitalize(), True, None
    return {
        "id": f"figure-{name}",
        "title": title,
        "kind": "figure",
        "analysis": "mean_profiles" if name.startswith("mean-") else "nca",
        "subject_level": not name.startswith("mean-"),
        "identifies_subjects": ids,
        "settings": "/mean_profiles" if name.startswith("mean-") else "/effective_settings",
        "replay": {"reproduced": False, "reason": "figures are drawn by the browser"},
        "table": table,
    }


def describe(path: str) -> dict[str, Any]:
    """The schema-2 fields (without path, media type, bytes and SHA-256) of ``path``."""
    p = PurePosixPath(path)
    fmt = p.suffix.lstrip(".").lower()
    base = {"format": fmt, "grouping_view": None}
    if path == "analysis.R":
        return base | {
            "id": "analysis-script",
            "title": "Script that reproduces the saved tables",
            "kind": "script",
            "analysis": "run",
            "subject_level": False,
            "identifies_subjects": False,
            "settings": None,
            "replay": {
                "reproduced": False,
                "reason": "this is the script that reproduces the tables",
            },
        }
    if path == "analysis-record.json":
        return base | {
            "id": "analysis-record",
            "title": "Record of the data, settings and choices of the run",
            "kind": "record",
            "analysis": "run",
            "subject_level": False,
            "identifies_subjects": False,
            "settings": None,
            "replay": {"reproduced": False, "reason": "this is the record of the run"},
        }
    if p.parent.as_posix() == "results/figures":
        return base | _figure(p.stem)
    id_ = p.stem
    if id_.startswith("opnca-summary-by-"):
        slug = id_.removeprefix("opnca-summary-by-")
        row = (
            f"Summary of the parameters by {slug}",
            "nca.summary",
            False,
            "/grouping",
            SUMMARY_NOTE,
        )
    else:
        row = _TABLES[id_]
    title, analysis, ids, settings, footnote = row
    table = base | {
        "id": id_,
        "title": title,
        "kind": "table",
        "analysis": analysis,
        "subject_level": ids,
        "identifies_subjects": ids,
        "settings": settings,
        "replay": _replay(id_),
        "columns": COLUMNS,
        "caption": None,
        "footnote": footnote,
    }
    if p.parent.as_posix() == "results/tables":  # the formatted flextable of a CSV table
        table |= {
            "id": f"{id_}-flextable",
            "title": f"{title} (formatted)",
            "replay": {"reproduced": False, "reason": f"formatted view of {id_}.csv"},
            "derived_from": id_,
        }
    return table


def listing(entries: list[dict[str, Any]]) -> dict[str, Any]:
    """A schema-2 ``artifacts.json`` around ``entries`` (path, media_type, bytes, sha256)."""
    flextables = any(e["path"].startswith("results/tables/") for e in entries)
    return {
        "schema_version": 2,
        "opnca_version": "0.3.1",
        "record": "analysis-record.json",
        "flextable": {"written": flextables, "reason": None if flextables else "not installed"},
        "artifacts": [e | describe(e["path"]) for e in entries],
    }
