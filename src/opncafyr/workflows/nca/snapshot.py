"""The frozen one-analyte input of a run (#25).

``create_run`` turns one checked selection into ``.opncafyr/runs/<run-id>/``:

- ``input/analysis.csv``: the selected rows, every cleaned column as written, plus the
  columns opnca-r reads (``NCA_CONC``, ``NCA_BLQ``, ``NCA_MISSING``, ``NCA_EXCLUDED``,
  ``NCA_EXCLUSION_REASON``). Rows are picked by string equality on the raw analyte value,
  then the raw specimen value: no trimming, no case folding (ADR 0005 Q1).
  ``NCA_CONC`` is the concentration when it is a number, else empty (BLQ text, blanks):
  never 0 (Q3). Excluded rows stay, marked (Q4).
- ``input/source-fingerprint.json`` (source path, size, SHA-256; no row data) and
  ``input/opnca-mapping.json`` (the opnca-r preset, #27).
- ``settings.json`` (``run-settings-v1``) and ``manifest.json`` (``run-manifest-v1``,
  state ``preparing``).

Everything is written in the run's staging folder (``core.runs.stage_run``), the input
files are made read-only, and the folder is renamed into place in one step
(``core.runs.publish_run``), so a half-written run never appears under its id. Nothing
here logs a value.
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
import stat
from pathlib import Path
from typing import Any

from opncafyr import __version__
from opncafyr.core.datasets import fingerprint
from opncafyr.core.manifest import atomic_write_json, atomic_write_text
from opncafyr.core.runs import RUN_ID_RE, RUNS_DIR, now, publish_run, stage_run
from opncafyr.core.workspace import resolve_in_root
from opncafyr.workflows.nca import mapping as M
from opncafyr.workflows.nca.checks import (
    BLQ_TEXT_PATTERNS,
    WARNING,
    Context,
    Selection,
    classify,
    finding_key,
    number,
    select,
)
from opncafyr.workflows.nca.preset import build_preset
from opncafyr.workflows.nca.recipe import NUMBER_RE, steps_hash

INPUT = "input/analysis.csv"
FINGERPRINT = "input/source-fingerprint.json"
PRESET = "input/opnca-mapping.json"
SETTINGS = "settings.json"
MANIFEST = "manifest.json"
SNAPSHOT_COLUMNS = {
    "CONC": "NCA_CONC",
    "BLQ": "NCA_BLQ",
    "MISSING": "NCA_MISSING",
    "EXCLUDED": "NCA_EXCLUDED",
    "EXCLUSION_REASON": "NCA_EXCLUSION_REASON",
}
BLQ_RULES = ("blq_predose", "blq_postdose", "blq_terminal")
RULE_VALUES = ("exclude", "zero", "half-lloq", "lloq-over-sqrt2")
METHODS = ("conventional", "censored_lognormal")
__all__ = ["RUN_ID_RE", "RUNS_DIR", "SnapshotError", "create_run"]

# R's reserved words: read.csv would rename a column called one of these.
_R_RESERVED = {
    "if",
    "else",
    "repeat",
    "while",
    "function",
    "for",
    "next",
    "break",
    "TRUE",
    "FALSE",
    "NULL",
    "Inf",
    "NaN",
    "NA",
    "NA_integer_",
    "NA_real_",
    "NA_character_",
    "NA_complex_",
    "in",
}
_R_NAME = re.compile(r"^(?:[A-Za-z]|\.(?![0-9]))[A-Za-z0-9._]*$")


class SnapshotError(ValueError):
    def __init__(self, code: str, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.extra = extra


def r_safe_name(name: str) -> bool:
    """True if R's ``read.csv`` keeps this header as it is (``check.names``)."""
    return bool(_R_NAME.match(name)) and name not in _R_RESERVED


def read_only(path: Path) -> None:
    path.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)


def _check_blq(blq: dict[str, Any] | None, method: str | None) -> dict[str, Any]:
    rules = dict(blq or {})
    missing = [r for r in BLQ_RULES if rules.get(r) not in RULE_VALUES]
    if missing or method not in METHODS or set(rules) - set(BLQ_RULES):
        raise SnapshotError(
            "needs_choice",
            "Choose all three BLQ rules and the BLQ method. There is no default.",
            missing=missing + ([] if method in METHODS else ["blq_method"]),
        )
    return {"rules": {r: rules[r] for r in BLQ_RULES}, "method": method}


def _numeric_ids(values: list[str]) -> bool:
    """True if R would read these IDs as numbers and merge two of them ("01" and "1")."""
    present = [v for v in values if v.strip()]
    if not present or not all(NUMBER_RE.match(v) for v in present):
        return False
    return len({float(v) for v in set(present)}) < len(set(present))


def build_rows(
    header: list[str],
    records: list[dict[str, str]],
    mapping: dict[str, Any],
    selection: Selection,
) -> tuple[list[str], list[list[str]]]:
    """The snapshot table: the selected rows with the opnca-r columns added."""
    for name in SNAPSHOT_COLUMNS.values():
        if name in header:
            raise SnapshotError(
                "column_clash", f"The data already has a column {name}; rename it first."
            )
    bad = [h for h in header if not r_safe_name(h)]
    if bad:
        raise SnapshotError(
            "column_names",
            "R would rename these columns when opnca-r reads the file: "
            f"{', '.join(bad[:5])}. Add a 'Clean column names' or 'Rename' step.",
        )
    ctx = Context(mapping=mapping, selection=selection, rows=[])
    classify(ctx, records)
    conc_col = M.role(mapping, "CONC")
    out_header = [*header, *SNAPSHOT_COLUMNS.values()]
    pattern = BLQ_TEXT_PATTERNS.get(
        ((mapping.get("flags") or {}).get("BLQ") or {}).get("text_pattern", "")
    )
    rows = []
    for row in ctx.rows:
        text = row.data.get(conc_col, "") if conc_col else ""
        conc = text if number(text) is not None and not (pattern and pattern.match(text)) else ""
        rows.append(
            [
                *(row.data.get(h, "") for h in header),
                conc,
                "TRUE" if row.blq else "FALSE",
                "TRUE" if row.missing else "FALSE",
                "TRUE" if row.excluded else "FALSE",
                row.reason if row.excluded else "",
            ]
        )
    return out_header, rows


def to_csv(header: list[str], rows: list[list[str]]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(header)
    writer.writerows(rows)
    return buf.getvalue()


def create_run(
    root: Path,
    workspace_doc: dict[str, Any],
    checked: dict[str, Any],
    selection: Selection,
    *,
    label: str | None,
    acknowledged: list[str],
    blq: dict[str, Any] | None,
    blq_method: str | None,
    parent_run_id: str | None = None,
) -> dict[str, Any]:
    """Write the run folder for an already checked and gated selection. Returns its manifest."""
    recipe = checked["recipe"]
    mapping = recipe.get("mapping") or {}
    blq_choice = _check_blq(blq, blq_method)
    occasion = M.role(mapping, "OCCASION")
    header, all_rows = checked["header"], checked["rows"]
    records, accounting = select(header, all_rows, mapping, selection)
    if occasion and len({r.get(occasion, "") for r in records}) > 1:
        raise SnapshotError(
            "occasion_unsupported",
            "This analyte has more than one occasion. opnca-r takes one profile per "
            "subject here: make one recipe per occasion with a 'Remove rows' step.",
        )
    subject_col = M.role(mapping, "SUBJECT_ID")
    if subject_col and _numeric_ids([r.get(subject_col, "") for r in records]):
        raise SnapshotError(
            "ids_collide",
            "Some subject IDs are the same number written differently (such as 01 and 1). "
            "R would merge them; make the IDs text first.",
        )
    out_header, out_rows = build_rows(header, records, mapping, selection)

    source = next(s for s in workspace_doc["sources"] if s["id"] == recipe["source_id"])
    source_path = resolve_in_root(root, source["path"])
    size, sha = fingerprint(source_path)
    if sha != source["sha256"] or size != source["size_bytes"]:
        raise SnapshotError(
            "source_changed", f"{source['path']} changed since it was imported. Import it again."
        )

    run_id, staging = stage_run(root)
    (staging / "input").mkdir()
    (staging / "logs").mkdir()
    created = now()

    text = to_csv(out_header, out_rows)
    atomic_write_text(staging / INPUT, text)
    snapshot_sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    atomic_write_json(
        staging / FINGERPRINT,
        {
            "path": source["path"],
            "size_bytes": size,
            "sha256": sha,
        },
    )
    preset = build_preset(mapping, SNAPSHOT_COLUMNS, blq_choice, out_header)
    atomic_write_json(staging / PRESET, preset)

    acks = []
    wanted = set(acknowledged)
    for f in checked["findings"]:
        key = f.get("key") or finding_key(f, checked["recipe_sha256"])
        if f["level"] == WARNING and key in wanted:
            acks.append(
                {
                    "key": key,
                    "check": f["check"],
                    "subject": f["subject"],
                    "lines": f["lines"],
                    "acknowledged_at": created,
                }
            )
    steps = checked.get("steps") or {}
    accounting = {
        "rows_source": int(steps.get("source_rows", len(all_rows))),
        "rows_rejected_by_recipe": int(steps.get("rows_rejected", 0)),
        **accounting,
        "rows_snapshot": len(out_rows),
        "rows_excluded": sum(1 for r in out_rows if r[-2] == "TRUE"),
    }
    analyte = {
        "column": M.role(mapping, "ANALYTE"),
        "value": selection.analyte_value,
        "label": (label or "").strip() or selection.analyte_value,
    }
    specimen_col = M.role(mapping, "SPECIMEN")
    specimen = (
        {"column": specimen_col, "value": selection.specimen_value}
        if specimen_col and selection.specimen_value is not None
        else None
    )
    settings = {
        "schema_version": "1.0",
        "run_id": run_id,
        "created_at": created,
        "parent_run_id": parent_run_id,
        "recipe": {
            "id": recipe["id"],
            "sha256": checked["recipe_sha256"],
            "steps_sha256": steps_hash(recipe),
        },
        "source": {"id": source["id"], "path": source["path"], "sha256": sha, "size_bytes": size},
        "analyte": analyte,
        "specimen": specimen,
        "mapping": mapping,
        "blq": blq_choice,
        "acknowledgements": acks,
        "snapshot": {
            "path": INPUT,
            "sha256": snapshot_sha,
            "rows": len(out_rows),
            "columns": dict(SNAPSHOT_COLUMNS),
        },
        "row_accounting": accounting,
    }
    atomic_write_json(staging / SETTINGS, settings, "run-settings-v1")
    settings_sha = hashlib.sha256((staging / SETTINGS).read_bytes()).hexdigest()
    manifest = {
        "schema_version": "1.0",
        "run_id": run_id,
        "created_at": created,
        "state": "preparing",
        "source": {"path": source["path"], "sha256": sha, "size_bytes": size},
        "recipe": {"id": recipe["id"], "sha256": checked["recipe_sha256"]},
        "lineage": {
            "recipe_sha256": checked["recipe_sha256"],
            "steps_sha256": steps_hash(recipe),
            "parent_run_id": parent_run_id,
        },
        "analysis_input": {"path": INPUT, "sha256": snapshot_sha, "rows": len(out_rows)},
        "analyte": analyte,
        "specimen": specimen,
        "mapping": mapping,
        "units": mapping.get("units") or {},
        "acknowledgements": acks,
        "row_accounting": accounting,
        "settings": {"path": SETTINGS, "sha256": settings_sha},
        "nca_settings": {"blq": blq_choice},
        "tools": {"opncafyr": __version__},
    }
    atomic_write_json(staging / MANIFEST, manifest, "run-manifest-v1")
    for name in (INPUT, FINGERPRINT, PRESET, SETTINGS):
        read_only(staging / name)
    publish_run(staging, run_id)
    return manifest
