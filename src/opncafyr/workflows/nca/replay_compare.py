"""Compare a replay's tables with the saved results (#29).

``analysis.R`` (written by opnca-r) re-runs the analysis on ``input/analysis.csv`` and
writes the same tables to ``replay/`` as the save wrote to ``results/``. Two tables
agree when they have the same columns in the same order and the same rows in the same
order, and every cell agrees:

- identifiers, labels, counts, flags and status (any text, and every column named in
  ``EXACT`` or starting with ``n_``) must be equal as text;
- other numbers agree within a relative tolerance: ``|a - b| <= rtol * max(|a|, |b|)``
  (default 1e-10, recorded with the result); blanks and ``NA`` must match exactly.

A table opnca-r's ``artifacts.json`` (schema 2, opnca-r 0.3.0) marks
``replay.reproduced: false`` (the bioequivalence tables, for one) is not compared; it is
listed under ``not_reproduced`` with opnca-r's reason (``not_reproduced``).

The report lists the first mismatches readably: file, row, column, saved and replayed
values. It lives in the run folder only; nothing here logs a value.
"""

from __future__ import annotations

import csv
import math
from pathlib import Path
from typing import Any

from opncafyr.workflows.nca.recipe import NUMBER_RE

DEFAULT_RTOL = 1e-10
MAX_LISTED = 25
EXACT = {
    "profile_id",
    "metric",
    "estimand",
    "unit",
    "status",
    "grouping",
    "view",
    "sensitivity_index",
}
MISSING = {"", "NA", "NaN"}


def _read(path: Path) -> tuple[list[str], list[list[str]]]:
    with path.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.reader(fh))
    return (rows[0], rows[1:]) if rows else ([], [])


def _exact(column: str) -> bool:
    return column in EXACT or column.startswith("n_")


def _agree(a: str, b: str, column: str, rtol: float) -> tuple[bool, float | None]:
    if a == b:
        return True, 0.0
    if _exact(column) or a in MISSING or b in MISSING:
        return False, None
    if not (NUMBER_RE.match(a) and NUMBER_RE.match(b)):
        return False, None
    x, y = float(a), float(b)
    scale = max(abs(x), abs(y))
    diff = abs(x - y)
    rel = 0.0 if scale == 0 else diff / scale
    return diff <= rtol * scale, rel


def compare_tables(saved: Path, replayed: Path, rtol: float = DEFAULT_RTOL) -> dict[str, Any]:
    out: dict[str, Any] = {
        "file": saved.name,
        "ok": False,
        "mismatches": [],
        "mismatch_count": 0,
        "max_rel_diff": 0.0,
    }
    if not replayed.is_file():
        out["problem"] = "The replay did not write this table."
        return out
    header_a, rows_a = _read(saved)
    header_b, rows_b = _read(replayed)
    out.update(rows=len(rows_a), replayed_rows=len(rows_b))
    if header_a != header_b:
        out["problem"] = "The columns differ."
        out["columns"] = {"saved": header_a, "replayed": header_b}
        return out
    if len(rows_a) != len(rows_b):
        out["problem"] = f"{len(rows_a)} rows were saved but {len(rows_b)} replayed."
        return out
    worst = 0.0
    for i, (ra, rb) in enumerate(zip(rows_a, rows_b, strict=True), start=1):
        for column, a, b in zip(header_a, ra, rb, strict=False):
            ok, rel = _agree(a, b, column, rtol)
            if rel is not None and not math.isnan(rel):
                worst = max(worst, rel)
            if not ok:
                out["mismatch_count"] += 1
                if len(out["mismatches"]) < MAX_LISTED:
                    out["mismatches"].append(
                        {"row": i, "column": column, "saved": a, "replayed": b, "rel_diff": rel}
                    )
    out["max_rel_diff"] = worst
    out["ok"] = out["mismatch_count"] == 0
    return out


def not_reproduced(listing: dict[str, Any]) -> dict[str, str]:
    """``results/<name>.csv`` -> reason, for each table ``analysis.R`` says it doesn't replay."""
    out: dict[str, str] = {}
    for art in listing.get("artifacts") or []:
        path = str(art.get("path", ""))
        replay = art.get("replay")
        if (
            isinstance(replay, dict)
            and replay.get("reproduced") is False
            and path.startswith("results/")
            and path.count("/") == 1
            and path.endswith(".csv")
        ):
            out[path.removeprefix("results/")] = str(replay.get("reason") or "")
    return out


def compare_folders(
    results: Path,
    replay: Path,
    rtol: float = DEFAULT_RTOL,
    skip: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Every table of ``results/`` (not its figures) against ``replay/``, except ``skip``."""
    skip = skip or {}
    files = sorted(p for p in results.glob("*.csv") if p.is_file() and p.name not in skip)
    tables = [compare_tables(p, replay / p.name, rtol) for p in files]
    return {
        "rtol": rtol,
        "status": "passed" if files and all(t["ok"] for t in tables) else "failed",
        "tables": tables,
        "not_reproduced": [
            {"file": name, "reason": reason}
            for name, reason in sorted(skip.items())
            if (results / name).is_file()
        ],
        "problem": None if files else "The saved run has no result tables.",
    }


def describe(report: dict[str, Any]) -> str:
    """One readable line per difference, for the page and the report."""
    lines = []
    for t in report["tables"]:
        if t.get("problem"):
            lines.append(f"{t['file']}: {t['problem']}")
        for m in t["mismatches"]:
            rel = "" if m["rel_diff"] is None else f" (relative difference {m['rel_diff']:.3g})"
            lines.append(
                f"{t['file']} row {m['row']}, {m['column']}: saved {m['saved']!r}, "
                f"replayed {m['replayed']!r}{rel}"
            )
        if t["mismatch_count"] > len(t["mismatches"]):
            lines.append(f"{t['file']}: {t['mismatch_count'] - len(t['mismatches'])} more")
    if report.get("problem"):
        lines.append(report["problem"])
    return "\n".join(lines)
