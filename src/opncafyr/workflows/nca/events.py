"""Event records (#58, ADR 0005 Q9): layout suggestions, the event stage's findings, and
the mapping rules for a recipe with an events block.

The stage itself is R (``r/nca_events_lib.R``), compiled into the recipe's ``clean.R``.
It puts every record in one partition and writes ``events.json``; the unresolved records
there become blocking findings here, global to every analyte of the recipe, each with the
choice or recipe fix that settles it. Nothing here chooses: a layout, a column or a code
meaning is a suggestion with its reasons until the user saves it.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from opncafyr.workflows.nca.recipe import (
    DOSE_COLUMN,
    DOSE_UNIT_COLUMN,
    EVENT_COLUMNS,
    LINE_COLUMN,
    ROUTE_COLUMN,
    TIME_AFTER_DOSE_COLUMN,
    clean_name,
)
from opncafyr.workflows.nca.units import header_hint

BLOCKING, WARNING, INFO = "blocking", "warning", "info"

# check id -> (message, fix hint). Every check of the stage blocks (ADR 0005 Q7, Q9).
MESSAGES: dict[str, tuple[str, str]] = {
    "event_kind_unknown": (
        "A record-kind value is not in the record-kind table.",
        "Say whether it is an observation, a dose or another record.",
    ),
    "event_endpoint_unknown": (
        "An observation's endpoint value is not in the endpoint table.",
        "Say whether it is a PK analyte or another endpoint.",
    ),
    "event_mdv_unmapped": (
        "An MDV value is not in the MDV table.",
        "Say whether it means no observation (true) or an observation (false).",
    ),
    "event_flag_conflict": (
        "The record kind, MDV and DV disagree: a dose with MDV 0 or a DV, or an observation "
        "with MDV 1 and a number in DV.",
        "Fix the source codes with 'Replace values', or exclude or remove the rows with a "
        "reason. The launcher never decides which column is right.",
    ),
    "event_time_not_numeric": (
        "A dose or PK observation has a time that is not a number.",
        "Fix it with a recipe step, or remove the row with a reason.",
    ),
    "event_dose_amount_invalid": (
        "A dose record has a missing, zero or negative amount.",
        "Fix the amount, or remove the record with a reason (a placebo dose is not a dose).",
    ),
    "event_excluded_dose": (
        "A dose record is excluded. Exclusion applies to observations only.",
        "Remove the dose record with a reason instead, so it is clear which dose counts.",
    ),
    "event_dose_unresolved": (
        "These observations belong to a subject whose dose records are unresolved.",
        "Settle the dose records first; then these observations are matched.",
    ),
    "event_multiple_doses": (
        "A subject has more than one dose, and no dosing interval is chosen.",
        "Choose the interval: the first dose, the last dose or a dose number.",
    ),
    "event_same_time_doses": (
        "Two or more dose records have the same time, and no rule for them is chosen.",
        "Choose to sum them as one dose, or remove the extra records with a reason.",
    ),
    "event_same_time_doses_differ": (
        "Dose records at the same time differ in route or unit, so they can't be summed.",
        "Fix the records, or remove all but one with a reason.",
    ),
    "event_predose": (
        "Samples were taken before the first dose, and no predose rule is chosen.",
        "Keep them (a negative time after dose) or set them aside.",
    ),
    "event_at_dose_time": (
        "A sample has exactly the time of a dose that starts or ends the chosen interval.",
        "Choose whether a sample at a dose time belongs before or after that dose.",
    ),
    "event_unmatched": (
        "PK observations have no dose for the chosen interval (no dose record, or fewer "
        "doses than the chosen dose number).",
        "Choose to reject them (to the rejected-row log), or fix the dose records.",
    ),
    "event_time_units_differ": (
        "An observation's time unit is not its dose's time unit.",
        "Add a recipe step so both use one unit.",
    ),
    "event_mapping_mismatch": (
        "With event records, these roles must use the prepared columns.",
        "Map the role to the column named in the message.",
    ),
    "event_role_unsupported": (
        "This role is not carried from dose records yet.",
        "Leave it unmapped for event-record data.",
    ),
    "analyte_endpoints_mixed": (
        "The selected rows hold more than one endpoint code.",
        "Map the endpoint column itself as the analyte, so each PK endpoint is its own run.",
    ),
    "event_nominal_basis": (
        "Nominal time is copied as written; it is not re-based on the chosen dose.",
        "Fine for a first-dose interval from time 0. Acknowledge, or leave it unmapped.",
    ),
    "event_accounting": ("Event records through the event stage.", ""),
}
LEVELS: dict[str, str] = {check: BLOCKING for check in MESSAGES}
LEVELS["event_nominal_basis"] = WARNING
LEVELS["event_accounting"] = INFO

# A check that one of the events block's choices settles: (choice, options).
CHOICES: dict[str, tuple[str, tuple[str, ...]]] = {
    "event_multiple_doses": ("dose_interval", ("first", "last", "number")),
    "event_same_time_doses": ("same_time_doses", ("sum",)),
    "event_predose": ("predose", ("keep", "set_aside")),
    "event_at_dose_time": ("observation_at_dose_time", ("before", "after")),
    "event_unmatched": ("unmatched", ("reject",)),
}


def _finding(check: str, analyte: str, lines: list[int], **extra: Any) -> dict[str, Any]:
    message, hint = MESSAGES[check]
    return {
        "check": check,
        "level": LEVELS[check],
        "analyte": analyte,
        "scope": "events",
        "subject": None,
        "lines": sorted(lines),
        "message": extra.pop("message", message),
        "fix_hint": hint,
        **extra,
    }


def findings(report: dict[str, Any] | None, analyte: str) -> list[dict[str, Any]]:
    """The event stage's unresolved records as blocking findings, and its accounting."""
    if not report:
        return []
    out = []
    for group in report.get("unresolved") or []:
        check = group["check"]
        if check not in MESSAGES:
            raise ValueError(f"events.json names an unknown check {check!r}")
        extra: dict[str, Any] = {}
        if check in CHOICES:
            extra = {"choice": CHOICES[check][0], "options": list(CHOICES[check][1])}
        out.append(_finding(check, analyte, list(group["lines"]), **extra))
    out.append(
        _finding(
            "event_accounting",
            analyte,
            [],
            accounting={
                "rows_in": report["rows_in"],
                **report["partitions"],
                "doses_used": report["doses_used"],
                "doses_unused": report["doses_unused"],
            },
        )
    )
    return out


def mapping_problems(mapping: dict[str, Any], events: dict[str, Any]) -> list[dict[str, Any]]:
    """What a mapping must say when the recipe has event records (#58)."""
    roles = mapping.get("roles") or {}
    units = mapping.get("units") or {}
    cols = events["columns"]
    out: list[dict[str, Any]] = []

    def mismatch(role: str, message: str) -> None:
        out.append(
            {"check": "event_mapping_mismatch", "level": BLOCKING, "role": role, "message": message}
        )

    wanted = {
        "ACTUAL_TIME": TIME_AFTER_DOSE_COLUMN,
        "DOSE": DOSE_COLUMN,
        "ROUTE": ROUTE_COLUMN,
        "SUBJECT_ID": cols["subject"],
    }
    if cols.get("occasion"):
        wanted["OCCASION"] = cols["occasion"]
    for role, column in wanted.items():
        if roles.get(role) and roles[role] != column:
            mismatch(role, f"With event records, map this role to {column}.")
    dose_unit = (units.get("DOSE") or {}).get("column")
    if cols.get("dose_unit") and roles.get("DOSE") and dose_unit != DOSE_UNIT_COLUMN:
        mismatch(
            "DOSE",
            f"The dose unit is read on dose records: use the unit column {DOSE_UNIT_COLUMN}.",
        )
    time_unit = (units.get("ACTUAL_TIME") or {}).get("column")
    if time_unit and cols.get("time_unit") and time_unit != cols["time_unit"]:
        mismatch("ACTUAL_TIME", f"The time unit column is {cols['time_unit']}.")
    for role in ("TAU", "INFUSION_DURATION"):
        if roles.get(role):
            out.append(
                {
                    "check": "event_role_unsupported",
                    "level": BLOCKING,
                    "role": role,
                    "message": f"{role} is not carried from dose records yet; leave it unmapped.",
                }
            )
    return out


def mapping_suggestions(events: dict[str, Any]) -> list[dict[str, Any]]:
    """Suggested roles for the prepared columns. The user still confirms each one."""
    reason = "Prepared by the event stage from the matched dose record."
    out = [
        {
            "role": "SUBJECT_ID",
            "column": events["columns"]["subject"],
            "confidence": 0.95,
            "reasons": ["The subject column of the event records."],
        },
        {
            "role": "ACTUAL_TIME",
            "column": TIME_AFTER_DOSE_COLUMN,
            "confidence": 0.95,
            "reasons": [f"{reason} Actual time minus the dose's actual time."],
        },
        {"role": "DOSE", "column": DOSE_COLUMN, "confidence": 0.95, "reasons": [reason]},
        {"role": "ROUTE", "column": ROUTE_COLUMN, "confidence": 0.95, "reasons": [reason]},
    ]
    if events["columns"].get("occasion"):
        out.append(
            {
                "role": "OCCASION",
                "column": events["columns"]["occasion"],
                "confidence": 0.95,
                "reasons": ["The occasion column of the event records."],
            }
        )
    return out


# --- Layout suggestion ------------------------------------------------------------------

ALIASES: dict[str, tuple[str, ...]] = {
    "subject": ("id", "usubjid", "subject", "subject_id", "subjid"),
    "time": ("time", "tafd", "afrlt", "tsfd", "atime"),
    "record_kind": ("evid",),
    "amount": ("amt",),
    "endpoint": ("cmt", "dvid", "ytype"),
    "dv": ("dv", "lidv", "conc", "cobs"),
    "mdv": ("mdv",),
    "occasion": ("occ", "occasion"),
    "dose_unit": ("eventu", "amtu", "amtunit", "doseu"),
    "time_unit": ("timeunit", "timeu", "tunit"),
    "route": ("route", "exroute"),
}
LABEL_ALIASES = ("name", "dvname", "endpoint_name", "analyte_name")
ANALYTE_ALIASES = ("pctestcd", "paramcd", "analyte", "dvid", "cmt")
# NONMEM's EVID 0, 1 and 2 have one meaning everywhere; 3 (reset) and 4 (reset and dose)
# change the system and are never proposed.
EVID_MEANINGS = {"0": "observation", "1": "dose", "2": "other"}
MAX_CODES = 50


def _find(header: list[str], aliases: tuple[str, ...]) -> list[str]:
    return [c for c in header if not c.startswith("opncafyr_") and clean_name(c) in aliases]


def suggest_layout(
    header: list[str], rows: list[list[str]], events: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Event records or one observation per row, with reasons; nothing is decided here.

    ``rows`` are the cleaned rows (every record, after the steps). With a saved ``events``
    block, codes are counted by its own columns and record kinds.
    """
    columns: dict[str, dict[str, Any]] = {}
    for part, aliases in ALIASES.items():
        found = _find(header, aliases)
        if found:
            columns[part] = {
                "column": found[0],
                "candidates": found,
                "reason": f"The name {found[0]} is a usual name for the {part.replace('_', ' ')}.",
            }
    reasons = []
    has_kind = "record_kind" in columns
    if has_kind:
        reasons.append(f"{columns['record_kind']['column']} gives each record's kind (EVID).")
    if "amount" in columns:
        reasons.append(f"{columns['amount']['column']} holds dose amounts on dose records (AMT).")
    if "mdv" in columns:
        reasons.append(f"{columns['mdv']['column']} marks records with no observation (MDV).")
    layout = (
        "event_records"
        if has_kind or ("amount" in columns and "mdv" in columns)
        else "observation_rows"
    )
    if layout == "observation_rows":
        reasons.append("No EVID, or AMT with MDV: one observation per row, mapped as it is.")

    chosen = (events or {}).get("columns") or {k: v["column"] for k, v in columns.items()}
    index = {name: i for i, name in enumerate(header)}

    def value(row: list[str], part: str) -> str | None:
        col = chosen.get(part)
        i = index.get(col) if col else None
        return row[i] if i is not None and i < len(row) else None

    kind_table = (events or {}).get("record_kinds") or {}
    kinds: Counter[str] = Counter()
    mdv: Counter[str] = Counter()
    endpoints: dict[str, dict[str, Any]] = {}
    label_col = next(iter(_find(header, LABEL_ALIASES)), None)
    li = index.get(label_col) if label_col else None
    for row in rows:
        kind_value = value(row, "record_kind")
        if kind_value is not None:
            kinds[kind_value] += 1
        mdv_value = value(row, "mdv")
        if mdv_value is not None:
            mdv[mdv_value] += 1
        meaning = kind_table.get(kind_value) if kind_table else EVID_MEANINGS.get(kind_value or "")
        endpoint = value(row, "endpoint")
        if endpoint is None:
            continue
        entry = endpoints.setdefault(
            endpoint,
            {"value": endpoint, "rows": Counter(), "labels": Counter(), "units": Counter()},
        )
        entry["rows"][meaning or "unknown"] += 1
        if li is not None and li < len(row):
            entry["labels"][row[li]] += 1
        unit = value(row, "dose_unit")
        if unit is not None:
            entry["units"][unit] += 1
    record_kinds = [
        {
            "value": v,
            "rows": n,
            "suggested": EVID_MEANINGS.get(v) if "record_kind" in columns else None,
            "saved": kind_table.get(v),
        }
        for v, n in sorted(kinds.items(), key=lambda kv: (-kv[1], kv[0]))[:MAX_CODES]
    ]
    endpoint_table = (events or {}).get("endpoints") or {}
    endpoint_list = [
        {
            "value": e["value"],
            "rows": dict(e["rows"]),
            "labels": [k for k, _ in e["labels"].most_common(3)],
            "units": [k for k, _ in e["units"].most_common(3)],
            "only_on_doses": set(e["rows"]) == {"dose"},
            "saved": endpoint_table.get(e["value"]),
        }
        for e in sorted(endpoints.values(), key=lambda e: e["value"])[:MAX_CODES]
    ]
    unit_hints = {
        c: hint
        for c in header
        for kind in ("time", "conc", "amount")
        if (hint := header_hint(c, kind))
    }
    return {
        "layout": layout,
        "reasons": reasons,
        "columns": columns,
        "record_kinds": record_kinds,
        "endpoints": endpoint_list,
        "mdv_values": [
            {"value": v, "rows": n, "saved": ((events or {}).get("mdv") or {}).get(v)}
            for v, n in sorted(mdv.items())[:MAX_CODES]
        ],
        "label_column": label_col,
        "needs_analyte": layout == "observation_rows" and not _find(header, ANALYTE_ALIASES),
        "unit_hints": unit_hints,
        "too_many_codes": len(kinds) > MAX_CODES or len(endpoints) > MAX_CODES,
    }


PREVIEW_COLUMNS = (LINE_COLUMN, *EVENT_COLUMNS)


def preview(header: list[str], rows: list[list[str]], events: dict[str, Any], n: int = 20):
    """The first ``n`` prepared observations: line, endpoint, time and the dose columns.
    No subject values (the page shows line numbers to look them up)."""
    cols = events["columns"]
    names = [LINE_COLUMN, cols["endpoint"], cols["time"], *EVENT_COLUMNS]
    idx = [header.index(c) for c in names if c in header]
    return {
        "columns": [header[i] for i in idx],
        "rows": [[r[i] for i in idx] for r in rows[:n]],
    }
