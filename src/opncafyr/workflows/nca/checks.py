"""Pre-analysis checks (#24, ADR 0005 Q7).

Checks run on the full cleaned data, after the exact analyte filter and the specimen
filter, before any snapshot or opnca-r session. A finding is::

    {check, level, analyte, scope, subject, lines, message, fix_hint,
     column?, value?, values?, kind?, role?}

``lines`` are physical lines of the source file (header = 1), never values; ``subject``
is the one subject the lines belong to, if there is one. Findings go to the page on this
origin; nothing here logs them.

Levels: blocking findings stop a run until a recipe or mapping change fixes them;
warnings need an explicit acknowledgement, recorded with the run; info is shown only.

Rules settled by the two-analyte fixture (``tests/fixtures/two_analyte.expected.json``):
value checks (time and concentration numbers, routes, flags) apply to every selected row,
excluded or not, because every row still reaches opnca-r's ``read.csv``; duplicate keys and
profile checks use the rows that are not excluded; analyte-level checks count every row.
A row whose flag value is outside its table is reported once, as ``flag_value_unmapped``.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from opncafyr.workflows.nca import events as E
from opncafyr.workflows.nca import mapping as M
from opncafyr.workflows.nca.recipe import (
    EXCLUDED_COLUMN,
    EXCLUSION_REASON_COLUMN,
    LINE_COLUMN,
    MISSING_TOKENS,
    NUMBER_RE,
    Lineage,
)
from opncafyr.workflows.nca.roles import BY_ID
from opncafyr.workflows.nca.routes import canonical_route
from opncafyr.workflows.nca.units import normalise

BLOCKING, WARNING, INFO = "blocking", "warning", "info"

# ADR 0005 Q7, plus the mapping problems of mapping.problems().
LEVELS: dict[str, str] = {
    "role_unmapped": BLOCKING,
    "analyte_no_rows": BLOCKING,
    "specimen_mixed": BLOCKING,
    "time_not_numeric": BLOCKING,
    "conc_not_numeric": BLOCKING,
    "conc_unclassified_blank": BLOCKING,
    "conc_negative": BLOCKING,
    "route_missing": BLOCKING,
    "route_unrecognised": BLOCKING,
    "route_varies": BLOCKING,
    "dose_varies": BLOCKING,
    "units_mixed": BLOCKING,
    "units_undeclared": BLOCKING,
    "duplicate_key": BLOCKING,
    "blq_and_missing": BLOCKING,
    "flag_value_unmapped": BLOCKING,
    "exclusion_no_reason": BLOCKING,
    "time_nominal_as_actual": BLOCKING,
    "time_clock_format": BLOCKING,
    "time_derivation_failed": BLOCKING,
    "profile_no_dose": BLOCKING,
    "flag_table_missing": BLOCKING,
    "column_reused": BLOCKING,
    "conc_zero_not_blq": WARNING,
    "blq_above_lloq": WARNING,
    "lloq_varies": WARNING,
    "time_negative": WARNING,
    "id_usubjid_not_one_to_one": WARNING,
    "session_exclusion_no_reason": WARNING,
    "time_not_sorted": INFO,
    "profile_flag_counts": INFO,
    "subject_accountability": INFO,
    "too_few_points": INFO,
    "opnca_format_check": INFO,
}

MESSAGES: dict[str, tuple[str, str]] = {
    "analyte_no_rows": (
        "No row has exactly this analyte value.",
        "Pick a value from the list; values are matched exactly.",
    ),
    "specimen_mixed": (
        "More than one specimen is left after the analyte filter.",
        "Map the specimen column and choose one specimen.",
    ),
    "time_not_numeric": (
        "Actual time is not a number.",
        "Fix it with a recipe step, or remove the row with a reason.",
    ),
    "conc_not_numeric": (
        "Concentration is not a number, not blank and not BLQ.",
        "Use 'Check numbers' for decimal commas, or remove the row with a reason.",
    ),
    "conc_unclassified_blank": (
        "A blank concentration is neither BLQ nor missing.",
        "Flag it BLQ or missing in the source data, or remove it with a reason.",
    ),
    "conc_negative": ("A negative concentration.", "Remove or exclude the row with a reason."),
    "route_missing": (
        "The route is blank.",
        "Routes are never filled in by the launcher. "
        "Remove the row, or recode the value, with a reason.",
    ),
    "route_unrecognised": (
        "opnca-r does not know this route.",
        "Add a 'Replace values' step that maps it to an opnca-r route.",
    ),
    "route_varies": ("A profile has more than one route.", "One route per profile."),
    "dose_varies": ("A profile has more than one dose.", "One dose per profile."),
    "units_mixed": (
        "Time, concentration or dose units differ within the analyte.",
        "Add a 'Convert units' step; equivalent units are not merged silently.",
    ),
    "units_undeclared": (
        "A unit is missing or not one the launcher knows.",
        "Declare the unit, or fix the unit column.",
    ),
    "duplicate_key": (
        "Two or more rows share a subject and actual time.",
        "Exclude or remove all but one, with a reason. Never resolved automatically.",
    ),
    "blq_and_missing": ("A row is flagged both BLQ and missing.", "A row is one or the other."),
    "flag_value_unmapped": (
        "A flag value is not in its value table.",
        "Say what the value means in the mapping.",
    ),
    "exclusion_no_reason": (
        "An excluded row has no reason.",
        "Add an 'Exclude rows' step with the reason.",
    ),
    "time_nominal_as_actual": (
        "The actual time column is nominal time.",
        "Map an actual elapsed time column; there is no override.",
    ),
    "time_clock_format": (
        "Actual time holds clock times, not elapsed time.",
        "Map elapsed time, or derive it with 'Derive elapsed time'.",
    ),
    "time_derivation_failed": (
        "Elapsed time could not be derived for these rows.",
        "Partial dates or mixed time zones: fix the dates first.",
    ),
    "profile_no_dose": (
        "A profile has no dose or no route.",
        "Add the dose, or exclude the profile with a reason.",
    ),
    "conc_zero_not_blq": (
        "A post-dose concentration of 0 without a BLQ flag.",
        "It may be a hidden BLQ. Acknowledge, or fix the flag.",
    ),
    "blq_above_lloq": (
        "A BLQ-flagged concentration above the LLOQ.",
        "The source may be inconsistent. Acknowledge, or fix it.",
    ),
    "lloq_varies": (
        "The LLOQ is not the same on every row.",
        "Can be right (assay change, dilution). Acknowledge if so.",
    ),
    "time_negative": ("A negative actual time.", "Predose samples are normal. Acknowledge."),
    "id_usubjid_not_one_to_one": (
        "The secondary ID does not match the subject ID one to one.",
        "Check the IDs. Acknowledge if expected.",
    ),
    "time_not_sorted": (
        "Times are not in order within a profile.",
        "Order doesn't change results.",
    ),
    "profile_flag_counts": ("BLQ, missing and excluded rows per profile.", ""),
    "subject_accountability": ("Subjects and rows through each filter.", ""),
    "too_few_points": (
        "Fewer than 3 measurable post-dose samples.",
        "opnca-r will warn in the session.",
    ),
}

# The event stage's checks (#58, ADR 0005 Q9).
LEVELS.update(E.LEVELS)
MESSAGES.update(E.MESSAGES)

CLOCK_RE = re.compile(r"^\s*\d{1,2}:\d{2}(:\d{2})?\s*$")
BLQ_TEXT_PATTERNS = {"^<": re.compile(r"^\s*<")}
NOMINAL_NAMES = set(BY_ID["NOMINAL_TIME"].aliases)


def is_blank(value: str) -> bool:
    return value.strip() in MISSING_TOKENS


def number(value: str) -> float | None:
    return float(value) if NUMBER_RE.match(value) else None


@dataclass(frozen=True)
class Selection:
    analyte_value: str
    specimen_value: str | None = None


@dataclass
class Row:
    line: int
    data: dict[str, str]
    blq: bool = False
    missing: bool = False
    excluded: bool = False
    reason: str = ""
    unmapped_flag: bool = False


@dataclass
class Context:
    mapping: dict[str, Any]
    selection: Selection
    rows: list[Row]
    lineage: dict[str, Lineage] = field(default_factory=dict)
    derivation_failures: dict[str, set[int]] = field(default_factory=dict)
    findings: list[dict[str, Any]] = field(default_factory=list)

    def col(self, role: str) -> str | None:
        return M.role(self.mapping, role)

    def get(self, row: Row, role: str) -> str | None:
        column = self.col(role)
        return row.data.get(column) if column else None

    def subject_of(self, rows: Iterable[Row]) -> str | None:
        subjects = {self.get(r, "SUBJECT_ID") for r in rows}
        return subjects.pop() if len(subjects) == 1 else None

    def add(self, check: str, scope: str, rows: list[Row], **extra: Any) -> None:
        message, hint = MESSAGES.get(check, ("", ""))
        self.findings.append(
            {
                "check": check,
                "level": LEVELS[check],
                "analyte": self.selection.analyte_value,
                "scope": scope,
                "subject": extra.pop("subject", self.subject_of(rows) if rows else None),
                "lines": sorted(r.line for r in rows),
                "message": extra.pop("message", message),
                "fix_hint": hint,
                **extra,
            }
        )


def finding_key(finding: dict[str, Any], recipe_sha256: str) -> str:
    """What a warning acknowledgement refers to: the check, analyte, lines and recipe."""
    text = json.dumps(
        [finding["check"], finding["analyte"], finding["subject"], finding["lines"], recipe_sha256],
        separators=(",", ":"),
    )
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


# --- Selection -----------------------------------------------------------------------


def select(
    header: list[str], rows: list[list[str]], mapping: dict[str, Any], selection: Selection
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    """The rows of one analyte (and specimen), exactly as written, with row accounting."""
    analyte = M.role(mapping, "ANALYTE")
    specimen = M.role(mapping, "SPECIMEN")
    records = [dict(zip(header, r, strict=False)) for r in rows]
    picked = [r for r in records if analyte and r.get(analyte) == selection.analyte_value]
    accounting: dict[str, Any] = {
        "rows_after_recipe": len(records),
        "rows_other_analytes": len(records) - len(picked),
        "rows_analyte": len(picked),
    }
    if specimen and selection.specimen_value is not None:
        kept = [r for r in picked if r.get(specimen) == selection.specimen_value]
        accounting["rows_other_specimens"] = len(picked) - len(kept)
        picked = kept
    accounting["rows_selected"] = len(picked)
    return picked, accounting


def classify(ctx: Context, records: list[dict[str, str]]) -> None:
    flags = ctx.mapping.get("flags") or {}
    conc_col = ctx.col("CONC")
    reason_col = ctx.col("EXCLUSION_REASON")
    for rec in records:
        row = Row(line=int(rec[LINE_COLUMN]), data=rec)
        for role in ("BLQ", "MISSING", "EXCLUDED"):
            column = ctx.col(role)
            table = (flags.get(role) or {}).get("table") or {}
            if not column or not table:
                continue
            value = rec.get(column, "")
            if value in table:
                setattr(row, role.lower(), bool(table[value]))
            else:
                row.unmapped_flag = True
                ctx.add("flag_value_unmapped", "row", [row], column=column, value=value)
        pattern = BLQ_TEXT_PATTERNS.get((flags.get("BLQ") or {}).get("text_pattern", ""))
        if pattern and conc_col and pattern.match(rec.get(conc_col, "")):
            row.blq = True
        if rec.get(EXCLUDED_COLUMN) == "TRUE":
            row.excluded = True
        recipe_reason = rec.get(EXCLUSION_REASON_COLUMN, "")
        if row.excluded:
            row.reason = (
                recipe_reason.strip()
                or (rec.get(reason_col, "").strip() if reason_col else "")
                or (flags.get("EXCLUDED") or {}).get("reason", "")
            )
        ctx.rows.append(row)


# --- The checks ----------------------------------------------------------------------


def _row_checks(ctx: Context) -> None:
    time_col, conc_col = ctx.col("ACTUAL_TIME"), ctx.col("CONC")
    route_col = ctx.col("ROUTE")
    clock: list[Row] = []
    for row in ctx.rows:
        if row.unmapped_flag:
            continue
        if row.blq and row.missing:
            ctx.add("blq_and_missing", "row", [row])
        if row.excluded and not row.reason:
            ctx.add("exclusion_no_reason", "row", [row])
        t = None
        if time_col:
            text = row.data.get(time_col, "")
            t = number(text)
            if t is None and CLOCK_RE.match(text):
                clock.append(row)
            elif t is None and row.line not in ctx.derivation_failures.get(time_col, set()):
                ctx.add("time_not_numeric", "row", [row])
            elif t is not None and t < 0:
                ctx.add("time_negative", "row", [row])
        if conc_col:
            text = row.data.get(conc_col, "")
            c = number(text)
            if is_blank(text):
                if not row.blq and not row.missing:
                    ctx.add("conc_unclassified_blank", "row", [row])
            elif c is None:
                if not row.blq:
                    ctx.add("conc_not_numeric", "row", [row])
            elif c < 0:
                ctx.add("conc_negative", "row", [row])
            elif c == 0 and not row.blq and not row.missing and t is not None and t > 0:
                ctx.add("conc_zero_not_blq", "row", [row])
            if row.blq and c is not None:
                lloq = number(ctx.get(row, "LLOQ") or "")
                if lloq is not None and c > lloq:
                    ctx.add("blq_above_lloq", "row", [row])
        if route_col:
            text = row.data.get(route_col, "")
            if not text.strip():
                ctx.add("route_missing", "row", [row])
            elif canonical_route(text) is None:
                ctx.add("route_unrecognised", "row", [row])
    if clock:
        ctx.add("time_clock_format", "analyte", clock)


def _first_seen(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _most_common_first(values: list[str]) -> list[str]:
    """Distinct values, the most common first, then in the order first seen."""
    counts = Counter(values)
    seen = _first_seen(values)
    top = max(seen, key=lambda v: (counts[v], -seen.index(v)))
    return [top, *(v for v in seen if v != top)]


def _minority(ctx: Context, rows: list[Row], key) -> tuple[list[Row], list[str]] | None:
    """Rows whose value differs from the most common one, and every value seen."""
    keyed = [(r, key(r)) for r in rows]
    keyed = [(r, k) for r, k in keyed if k]
    if len(set(k for _, k in keyed)) < 2:
        return None
    values = _most_common_first([k for _, k in keyed])
    return [r for r, k in keyed if k != values[0]], values


def _analyte_checks(ctx: Context) -> None:
    lloq = _minority(ctx, ctx.rows, lambda r: (ctx.get(r, "LLOQ") or "").strip())
    if lloq:
        ctx.add("lloq_varies", "analyte", lloq[0], values=", ".join(lloq[1]))
    units = ctx.mapping.get("units") or {}
    for role, kind in (("ACTUAL_TIME", "time"), ("CONC", "conc"), ("DOSE", "amount")):
        column = (units.get(role) or {}).get("column")
        if not column or not ctx.col(role):
            continue
        unknown = [
            r
            for r in ctx.rows
            if r.data.get(column, "").strip() and not normalise(r.data[column], kind)
        ]
        if unknown:
            ctx.add(
                "units_undeclared",
                "analyte",
                unknown,
                role=role,
                values=", ".join(_first_seen(r.data[column] for r in unknown)),
            )
        mixed = _minority(
            ctx, ctx.rows, lambda r, c=column, k=kind: normalise(r.data.get(c, ""), k) or ""
        )
        if mixed:
            ctx.add("units_mixed", "analyte", mixed[0], role=role, values=", ".join(mixed[1]))
    second = ctx.col("SECONDARY_ID")
    if second and ctx.col("SUBJECT_ID"):
        by_subject: dict[str, list[Row]] = defaultdict(list)
        for r in ctx.rows:
            by_subject[ctx.get(r, "SUBJECT_ID") or ""].append(r)
        for subject, rows in by_subject.items():
            odd = _minority(ctx, rows, lambda r: r.data.get(second, ""))
            if odd:
                ctx.add(
                    "id_usubjid_not_one_to_one",
                    "subject",
                    odd[0],
                    subject=subject,
                    values=", ".join(odd[1]),
                )
        owners: dict[str, set[str]] = defaultdict(set)
        for r in ctx.rows:
            owners[r.data.get(second, "")].add(ctx.get(r, "SUBJECT_ID") or "")
        for value, subjects in owners.items():
            if value.strip() and len(subjects) > 1:
                rows = [r for r in ctx.rows if r.data.get(second, "") == value]
                ctx.add(
                    "id_usubjid_not_one_to_one",
                    "subject",
                    rows,
                    subject=None,
                    values=", ".join(sorted(subjects)),
                )


def _profiles(ctx: Context) -> dict[tuple[str, ...], list[Row]]:
    roles = ("SUBJECT_ID", "OCCASION", "SPECIMEN")
    out: dict[tuple[str, ...], list[Row]] = defaultdict(list)
    for r in ctx.rows:
        if not r.excluded:
            out[tuple(ctx.get(r, role) or "" for role in roles)].append(r)
    return out


def _profile_checks(ctx: Context) -> None:
    time_col, conc_col = ctx.col("ACTUAL_TIME"), ctx.col("CONC")
    counts = []
    for rows in _profiles(ctx).values():
        doses = [v for r in rows if (v := (ctx.get(r, "DOSE") or "").strip())]
        routes = [v for r in rows if (v := (ctx.get(r, "ROUTE") or "").strip())]
        doses = _most_common_first(doses) if doses else []
        routes = _most_common_first(routes) if routes else []
        if (ctx.col("DOSE") and not doses) or (ctx.col("ROUTE") and not routes):
            ctx.add("profile_no_dose", "profile", rows)
        if len(doses) > 1:
            ctx.add("dose_varies", "profile", rows, values=", ".join(doses))
        if len(routes) > 1:
            ctx.add("route_varies", "profile", rows, values=", ".join(routes))
        if time_col:
            unsorted: set[int] = set()
            last: tuple[Row, float] | None = None
            for r in rows:
                t = number(r.data.get(time_col, ""))
                if t is None:
                    continue
                if last is not None and t < last[1]:
                    unsorted.update((last[0].line, r.line))
                last = (r, t)
            if unsorted:
                ctx.add("time_not_sorted", "profile", [r for r in rows if r.line in unsorted])
        if time_col and conc_col:
            measurable = 0
            for r in rows:
                t, c = number(r.data.get(time_col, "")), number(r.data.get(conc_col, ""))
                if t is not None and t > 0 and c is not None and c > 0 and not (r.blq or r.missing):
                    measurable += 1
            if measurable < 3:
                ctx.add("too_few_points", "profile", rows)
        counts.append(
            {
                "subject": ctx.get(rows[0], "SUBJECT_ID"),
                "rows": len(rows),
                "blq": sum(r.blq for r in rows),
                "missing": sum(r.missing for r in rows),
            }
        )
    excluded = Counter(ctx.get(r, "SUBJECT_ID") for r in ctx.rows if r.excluded)
    for entry in counts:
        entry["excluded"] = excluded.get(entry["subject"], 0)
    if counts:
        ctx.findings.append(
            {
                "check": "profile_flag_counts",
                "level": INFO,
                "analyte": ctx.selection.analyte_value,
                "scope": "analyte",
                "subject": None,
                "lines": [],
                "message": MESSAGES["profile_flag_counts"][0],
                "fix_hint": "",
                "profiles": counts,
            }
        )


def _duplicates(ctx: Context) -> None:
    time_col = ctx.col("ACTUAL_TIME")
    if not time_col:
        return
    groups: dict[tuple, list[Row]] = defaultdict(list)
    for key, rows in _profiles(ctx).items():
        for r in rows:
            text = r.data.get(time_col, "")
            t = number(text)
            groups[(key, t if t is not None else text)].append(r)
    for rows in groups.values():
        if len(rows) < 2:
            continue
        bodies = {
            json.dumps({k: v for k, v in r.data.items() if k != LINE_COLUMN}, sort_keys=True)
            for r in rows
        }
        ctx.add("duplicate_key", "key", rows, kind="exact" if len(bodies) == 1 else "conflicting")


def _time_role_checks(ctx: Context) -> None:
    actual, nominal = ctx.col("ACTUAL_TIME"), ctx.col("NOMINAL_TIME")
    if not actual:
        return
    origin = (ctx.lineage.get(actual) or Lineage(actual)).origin or ""
    names = {actual.lower(), origin.lower()}
    if (nominal and nominal in (actual, origin)) or names & NOMINAL_NAMES:
        ctx.add("time_nominal_as_actual", "analyte", [], subject=None)
    failed = ctx.derivation_failures.get(actual, set())
    by_subject: dict[str | None, list[Row]] = defaultdict(list)
    for r in ctx.rows:
        if r.line in failed:
            by_subject[ctx.get(r, "SUBJECT_ID")].append(r)
    for subject, rows in by_subject.items():
        ctx.add("time_derivation_failed", "row", rows, subject=subject)


def _specimen_check(ctx: Context, records: list[dict[str, str]], header: list[str]) -> None:
    specimen = ctx.col("SPECIMEN")
    if specimen and ctx.selection.specimen_value is not None:
        return
    candidates = (
        [specimen] if specimen else [c for c in header if c.lower() in BY_ID["SPECIMEN"].aliases]
    )
    for column in candidates:
        split = _minority(ctx, ctx.rows, lambda r, c=column: r.data.get(c, ""))
        if split:
            ctx.add(
                "specimen_mixed",
                "analyte",
                split[0],
                column=column,
                values=", ".join(split[1]),
                subject=None,
            )


def _event_checks(ctx: Context, events: dict[str, Any], report: dict[str, Any] | None) -> None:
    ctx.findings.extend(E.findings(report, ctx.selection.analyte_value))
    endpoint = events["columns"]["endpoint"]
    if ctx.col("ANALYTE") and ctx.col("ANALYTE") != endpoint:
        mixed = _minority(ctx, ctx.rows, lambda r: r.data.get(endpoint, ""))
        if mixed:
            ctx.add(
                "analyte_endpoints_mixed",
                "analyte",
                mixed[0],
                subject=None,
                column=endpoint,
                values=", ".join(mixed[1]),
            )
    if ctx.col("NOMINAL_TIME"):
        ctx.add("event_nominal_basis", "analyte", [], subject=None)


def run_checks(
    header: list[str],
    rows: list[list[str]],
    mapping: dict[str, Any] | None,
    selection: Selection,
    *,
    lineage: dict[str, Lineage] | None = None,
    steps: dict[str, Any] | None = None,
    events: dict[str, Any] | None = None,
    events_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Every check for one analyte selection. ``steps`` is the cleaning ``steps.json``.

    For a recipe with an events block (#58), ``header`` and ``rows`` are the prepared
    observations, ``events`` the block and ``events_report`` its ``events.json``: every
    unresolved record blocks, whatever the analyte.
    """
    mapping = mapping or M.empty_mapping()
    ctx = Context(mapping=mapping, selection=selection, rows=[], lineage=lineage or {})
    for problem in M.problems(mapping, events):
        ctx.findings.append(
            {
                "analyte": selection.analyte_value,
                "scope": "mapping",
                "subject": None,
                "lines": [],
                "fix_hint": "Change the mapping.",
                **problem,
            }
        )
    for step in (steps or {}).get("steps", []):
        for issue in step.get("issues", []):
            if issue["kind"] == "time_derivation_failed":
                ctx.derivation_failures.setdefault(issue["column"], set()).update(issue["lines"])
    records, accounting = select(header, rows, mapping, selection)
    if M.role(mapping, "ANALYTE") and not records and accounting["rows_analyte"] == 0:
        ctx.add("analyte_no_rows", "analyte", [], subject=None)
    classify(ctx, records)
    _specimen_check(ctx, records, header)
    _time_role_checks(ctx)
    _row_checks(ctx)
    _analyte_checks(ctx)
    _duplicates(ctx)
    _profile_checks(ctx)
    if events:
        _event_checks(ctx, events, events_report)
    subjects = {ctx.get(r, "SUBJECT_ID") for r in ctx.rows}
    ctx.findings.append(
        {
            "check": "subject_accountability",
            "level": INFO,
            "analyte": selection.analyte_value,
            "scope": "analyte",
            "subject": None,
            "lines": [],
            "message": MESSAGES["subject_accountability"][0],
            "fix_hint": "",
            "accounting": {
                **accounting,
                "subjects_selected": len(subjects - {None}),
                "rows_excluded": sum(r.excluded for r in ctx.rows),
            },
        }
    )
    order = {BLOCKING: 0, WARNING: 1, INFO: 2}
    ctx.findings.sort(key=lambda f: (order[f["level"]], f["lines"][:1] or [0], f["check"]))
    summary = Counter(f["level"] for f in ctx.findings)
    return {
        "findings": ctx.findings,
        "summary": {level: summary.get(level, 0) for level in (BLOCKING, WARNING, INFO)},
        "accounting": accounting,
    }


def gate(
    findings: list[dict[str, Any]], acknowledged: Iterable[str], recipe_sha256: str
) -> tuple[str, list[dict[str, Any]]]:
    """``("ok", [])``, or why a run can't start: ``checks_blocking`` or
    ``warnings_unacknowledged`` with the findings concerned."""
    blocking = [f for f in findings if f["level"] == BLOCKING]
    if blocking:
        return "checks_blocking", blocking
    acked = set(acknowledged)
    open_ = [
        f for f in findings if f["level"] == WARNING and finding_key(f, recipe_sha256) not in acked
    ]
    if open_:
        return "warnings_unacknowledged", open_
    return "ok", []
