"""Candidate analyte columns and their exact values, for the dataset preview (#21).

A suggestion only: the user maps the analyte column and picks one value (#23, #25).
Code columns follow ADR 0005 Q1 (``PCTESTCD``, ``PARAMCD``, ``ANALYTE``, ``DVID``,
``CMT``). Values are the raw text, because the run filter is exact string equality:
``"METAB "`` is not ``"METAB"``. Values that differ only by surrounding spaces or letter
case are flagged as variants, never merged.
"""

from __future__ import annotations

import re
from typing import Any

from opncafyr.core.datasets import DatasetPreview

ANALYTE_CODE_COLUMNS = ("pctestcd", "paramcd", "analyte", "dvid", "cmt")


def clean_name(name: str) -> str:
    """Lower case, with runs of other characters as one underscore (``PC TESTCD`` ->
    ``pc_testcd``)."""
    return re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")


class AnalyteAnnotator:
    id = "analytes"

    def annotate(self, preview: DatasetPreview) -> dict[str, Any]:
        found: list[tuple[int, dict[str, Any]]] = []
        for column in preview.columns:
            key = clean_name(column.name)
            if key not in ANALYTE_CODE_COLUMNS:
                continue
            entry: dict[str, Any] = {"column": column.name}
            if column.values_capped:
                entry |= {"values": [], "too_many_values": True, "variants": []}
            else:
                groups: dict[str, list[str]] = {}
                for v in column.values:
                    groups.setdefault(v.value.strip().casefold(), []).append(v.value)
                entry |= {
                    "values": [{"value": v.value, "rows": v.count} for v in column.values],
                    "too_many_values": False,
                    "variants": [sorted(g) for g in groups.values() if len(g) > 1],
                }
            found.append((ANALYTE_CODE_COLUMNS.index(key), entry))
        return {
            "columns": [entry for _, entry in sorted(found, key=lambda f: f[0])],
            "from_sample": not preview.row_count_exact,
            "note": "Suggestions only. You choose the analyte column and value when you "
            "set up a run.",
        }
