"""Cleaning recipes (#22): load, validate, plan columns, hash.

A recipe is ``.opncafyr/recipes/<id>.yaml`` (schema ``recipe-v1``): ordered steps that
carry data, never code. The schema fixes each step's shape; this module adds the rules a
schema can't express, against the source's header:

- every column a step reads exists at that point; targets of ``derive``, ``flag`` and
  ``derive_elapsed_time`` are new columns, and ``rename`` never reuses a name the data has
  had (so no step can overwrite actual time with nominal time, even through a swap);
- no step writes a numeric zero for a BLQ-looking or blank value, and no constant zero
  is derived (ADR 0005 Q3: "the launcher never writes 0");
- the fixed columns ``opncafyr_line`` (physical source line) and the exclusion pair
  (``opncafyr_excluded`` / ``opncafyr_exclusion_reason``, written only by ``exclude``)
  can't be renamed, recoded or derived.

``plan_columns`` also returns each column's lineage (which source column it came from
and through which ops), which the pre-analysis checks use to block nominal-as-actual time.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any

import yaml
from jsonschema import Draft202012Validator
from jsonschema.exceptions import best_match

from opncafyr.core.manifest import load_schema

RECIPE_SCHEMA = "recipe-v1"
RECIPES_DIR = ".opncafyr/recipes"
LINE_COLUMN = "opncafyr_line"
EXCLUDED_COLUMN = "opncafyr_excluded"
EXCLUSION_REASON_COLUMN = "opncafyr_exclusion_reason"
FIXED_COLUMNS = (LINE_COLUMN, EXCLUDED_COLUMN, EXCLUSION_REASON_COLUMN)
RECIPE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")

# The importer's missing-value tokens (importers/csv.py), the same set R is given.
MISSING_TOKENS = ("", "NA", ".", "NaN", "NULL")
# Text that reads as below the limit of quantification (ADR 0005 Q3).
BLQ_TEXT_RE = re.compile(r"^\s*(<|blq$|bloq$|bql$)", re.IGNORECASE)
# A number R's read.csv reads as numeric; also used by checks and the snapshot.
NUMBER_RE = re.compile(r"^[+-]?([0-9]+\.?[0-9]*|\.[0-9]+)([eE][+-]?[0-9]+)?$")
INTEGER_RE = re.compile(r"^[+-]?[0-9]+$")

# Unit conversion factors, to ng/mL for concentrations and to mg for amounts. Only
# conversions within one table are allowed; nothing else is ever converted (ADR 0005 Q2).
CONC_FACTORS = {
    "pg/mL": 1e-3,
    "ng/L": 1e-3,
    "ng/mL": 1.0,
    "ug/L": 1.0,
    "ug/mL": 1e3,
    "mg/L": 1e3,
    "mg/mL": 1e6,
    "g/L": 1e6,
}
AMOUNT_FACTORS = {"ng": 1e-6, "ug": 1e-3, "mg": 1.0, "g": 1e3}


class RecipeError(ValueError):
    """A recipe that can't be saved or run, with the step it is about (1-based)."""

    def __init__(self, message: str, step: int | None = None) -> None:
        super().__init__(message)
        self.step = step

    def to_dict(self) -> dict[str, Any]:
        return {"message": str(self), "step": self.step}


@dataclass
class Lineage:
    """Where a column came from: the source column and the ops applied to it."""

    origin: str | None
    ops: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"origin": self.origin, "ops": list(self.ops)}


@dataclass
class ColumnPlan:
    columns: list[str]
    lineage: dict[str, Lineage]


def new_recipe(recipe_id: str, source_id: str) -> dict[str, Any]:
    return {"schema_version": "1.0", "id": recipe_id, "source_id": source_id, "steps": []}


def recipe_hash(doc: dict[str, Any]) -> str:
    """SHA-256 of the canonical JSON form (sorted keys, no spaces, UTF-8)."""
    canonical = json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def loads(text: str) -> dict[str, Any]:
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise RecipeError(f"The recipe is not valid YAML: {exc}") from None
    validate_schema(doc)
    return doc


def dumps(doc: dict[str, Any]) -> str:
    header = "# opncafyr cleaning recipe (recipe-v1). Steps are data, never run as code.\n"
    return header + yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=100)


def validate_schema(doc: Any) -> None:
    validator = Draft202012Validator(load_schema(RECIPE_SCHEMA))
    error = best_match(validator.iter_errors(doc))
    if error is None:
        return
    step = None
    path = list(error.absolute_path)
    if len(path) >= 2 and path[0] == "steps" and isinstance(path[1], int):
        step = path[1] + 1
    where = "/".join(str(p) for p in path) or "recipe"
    raise RecipeError(f"{where}: {error.message}", step)


def clean_name(name: str) -> str:
    """The ``clean_names`` rule, identical in R (nca_clean_lib.R ``oc_clean_name``).

    ASCII letters are lower-cased, every run of other characters becomes ``_``, leading
    and trailing ``_`` go, an empty result is ``x`` and a leading digit gets an ``x``.
    """
    lowered = name.translate(_ASCII_LOWER)
    cleaned = re.sub(r"[^a-z0-9]+", "_", lowered).strip("_")
    if not cleaned:
        cleaned = "x"
    if cleaned[0].isdigit():
        cleaned = "x" + cleaned
    return cleaned


_ASCII_LOWER = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")


def clean_names(names: list[str]) -> list[str]:
    """``clean_name`` for every column, with ``_2``, ``_3``... for repeats."""
    out: list[str] = []
    seen: dict[str, int] = {}
    for name in names:
        if name in FIXED_COLUMNS:
            out.append(name)
            continue
        base = clean_name(name)
        candidate = base
        n = seen.get(base, 1)
        while candidate in out or candidate in FIXED_COLUMNS:
            n += 1
            candidate = f"{base}_{n}"
        seen[base] = n
        out.append(candidate)
    return out


def is_numeric_zero(value: str) -> bool:
    text = value.strip()
    return bool(NUMBER_RE.match(text)) and float(text) == 0.0


def unit_table(unit: str) -> dict[str, float] | None:
    for table in (CONC_FACTORS, AMOUNT_FACTORS):
        if unit in table:
            return table
    return None


def _where_columns(where: dict[str, Any]) -> list[str]:
    if "all" in where:
        return [c for w in where["all"] for c in _where_columns(w)]
    return [where["column"]]


def step_reads(step: dict[str, Any]) -> list[str]:
    """Columns a step reads (must exist before it runs)."""
    op, a = step["op"], step["args"]
    if op in ("rename",):
        return [a["from"]]
    if op in ("trim", "report_dupes"):
        return list(a["columns"])
    if op in ("parse_type", "recode", "flag"):
        return [a["column"]]
    if op == "derive":
        if a["fn"] == "copy":
            return [a["from"]]
        if a["fn"] == "concat":
            return list(a["from"])
        return []
    if op == "derive_elapsed_time":
        return [a["from"], a["reference"]]
    if op == "convert_units":
        return [a["value_column"], a["unit_column"]]
    if op in ("exclude", "filter_rows"):
        return _where_columns(a["where"])
    return []


def plan_columns(doc: dict[str, Any], header: list[str]) -> ColumnPlan:
    """Check every step against the columns it will see; return the final columns.

    ``header`` is the source's header as read (before ``opncafyr_line`` is added).
    Raises ``RecipeError`` for the first step that breaks a rule.
    """
    validate_schema(doc)
    for name in header:
        if name in FIXED_COLUMNS:
            raise RecipeError(f"The source already has a column named {name}, which is reserved.")
    if len(set(header)) != len(header):
        raise RecipeError("The source has two columns with the same name. Fix the header first.")
    columns = [LINE_COLUMN, *header]
    lineage = {c: Lineage(c) for c in header}
    lineage[LINE_COLUMN] = Lineage(None, ["line"])
    ever: set[str] = set(columns)

    for index, step in enumerate(doc["steps"], start=1):
        op, a = step["op"], step["args"]

        def fail(message: str, index: int = index, op: str = op) -> RecipeError:
            return RecipeError(f"Step {index} ({op}): {message}", index)

        for name in step_reads(step):
            if name not in columns:
                raise fail(f"there is no column {name} at this point.")
        writes = _step_writes(step)
        for name in writes:
            if name in FIXED_COLUMNS and op != "exclude":
                raise fail(f"{name} is managed by the launcher and can't be changed.")
        if op == "clean_names":
            renamed = clean_names(columns)
            lineage = {new: lineage[old] for old, new in zip(columns, renamed, strict=True)}
            for lin in lineage.values():
                lin.ops.append("clean_names")
            columns = renamed
        elif op == "rename":
            if a["to"] in ever:
                raise fail(
                    f"{a['to']} is, or was, a column name. Renaming onto it could replace "
                    "data; choose a new name."
                )
            columns[columns.index(a["from"])] = a["to"]
            lineage[a["to"]] = lineage.pop(a["from"])
            lineage[a["to"]].ops.append("rename")
        elif op in ("derive", "derive_elapsed_time", "flag"):
            target = a["target"]
            if target in ever:
                raise fail(
                    f"{target} is, or was, a column name. Derived values always go into a "
                    "new column, so no column is overwritten."
                )
            if op == "derive" and a["fn"] == "constant" and is_numeric_zero(a["value"]):
                raise fail("a constant zero is never derived (ADR 0005 Q3: never write 0).")
            columns.append(target)
            if op == "derive" and a["fn"] == "copy":
                src = lineage[a["from"]]
                lineage[target] = Lineage(src.origin, [*src.ops, "copy"])
            else:
                lineage[target] = Lineage(None, [op])
        elif op == "recode":
            for old, new in a["map"].items():
                if is_numeric_zero(new) and (
                    BLQ_TEXT_RE.match(old) or old.strip() in MISSING_TOKENS
                ):
                    raise fail(
                        f'recoding "{old}" to zero would set a BLQ or missing value to 0, '
                        "which is never done (ADR 0005 Q3). Map BLQ with a flag instead."
                    )
            lineage[a["column"]].ops.append("recode")
        elif op == "convert_units":
            src, dst = a["from"], a["to"]
            table = unit_table(src)
            if table is None or dst not in table:
                raise fail(
                    f"{src} to {dst} is not a conversion the launcher knows. Units are "
                    "never converted by guesswork; use recode if the values are the same."
                )
            lineage[a["value_column"]].ops.append("convert_units")
        elif op == "exclude":
            for name in (EXCLUDED_COLUMN, EXCLUSION_REASON_COLUMN):
                if name not in columns:
                    columns.append(name)
                    lineage[name] = Lineage(None, ["exclude"])
        elif op in ("trim", "parse_type"):
            for name in writes:
                lineage[name].ops.append(op)
        ever.update(columns)
    return ColumnPlan(columns=columns, lineage=lineage)


def _step_writes(step: dict[str, Any]) -> list[str]:
    op, a = step["op"], step["args"]
    if op == "rename":
        return [a["from"]]
    if op == "trim":
        return list(a["columns"])
    if op in ("parse_type", "recode"):
        return [a["column"]]
    if op in ("derive", "derive_elapsed_time", "flag"):
        return [a["target"]]
    if op == "convert_units":
        return [a["value_column"], a["unit_column"]]
    if op == "exclude":
        return [EXCLUDED_COLUMN, EXCLUSION_REASON_COLUMN]
    return []


def check(doc: dict[str, Any], header: list[str]) -> ColumnPlan:
    """Schema plus semantic rules. The single entry point before a save or a run."""
    if not RECIPE_ID_RE.match(str(doc.get("id", ""))):
        raise RecipeError("A recipe id is lower-case letters, digits and dashes.")
    return plan_columns(doc, header)
