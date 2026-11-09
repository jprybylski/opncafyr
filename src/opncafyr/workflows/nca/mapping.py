"""The mapping block of a recipe (#23): which column plays each role, the units and the
value tables of the flag columns. The user confirms every entry; this module only checks.

Shape (``recipe-v1`` ``$defs/mapping``)::

    roles:  {SUBJECT_ID: USUBJID, ACTUAL_TIME: AFRLT, ..., DOSE: null}
    units:  {ACTUAL_TIME: {value: h}, CONC: {column: PCSTRESU}, DOSE: {column: DOSEU}}
    flags:  {BLQ: {table: {"Y": true, "": false}, text_pattern: "^<"},
             MISSING: {table: {...}}, EXCLUDED: {table: {...}, reason: "..."}}

The flag columns are the BLQ, MISSING and EXCLUDED roles; the exclusion reason is the
EXCLUSION_REASON role, or ``flags.EXCLUDED.reason`` for all rows. A unit is a per-row
column or one declared value, never inferred (ADR 0005 Q2).
"""

from __future__ import annotations

from typing import Any

from opncafyr.workflows.nca.roles import BY_ID, REQUIRED
from opncafyr.workflows.nca.units import UNIT_ROLES, normalise

FLAG_ROLES = ("BLQ", "MISSING", "EXCLUDED")


def empty_mapping() -> dict[str, Any]:
    return {"roles": {}}


def role(mapping: dict[str, Any] | None, name: str) -> str | None:
    return ((mapping or {}).get("roles") or {}).get(name)


def mapping_columns(mapping: dict[str, Any]) -> list[tuple[str, str]]:
    """``(where, column)`` for every column the mapping names."""
    out = [(f"role {r}", c) for r, c in (mapping.get("roles") or {}).items() if c]
    for r, unit in (mapping.get("units") or {}).items():
        if "column" in unit:
            out.append((f"{r} unit", unit["column"]))
    return out


def problems(
    mapping: dict[str, Any] | None, events: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    """What stops the mapping from being used, as findings-style dicts (ADR 0005 Q7).

    With an events block (#58), also ``event_mapping_mismatch`` when time, dose, route or
    subject don't use the prepared columns, and ``event_role_unsupported`` for TAU and
    INFUSION_DURATION (``events.mapping_problems``).

    ``role_unmapped`` for a required role; ``units_undeclared`` for a mapped time,
    concentration or dose without a unit, or with a declared unit the launcher doesn't
    know; ``flag_table_missing`` for a mapped flag column with no value table; and
    ``column_reused`` when one column plays two roles.
    """
    mapping = mapping or {}
    roles = mapping.get("roles") or {}
    units = mapping.get("units") or {}
    flags = mapping.get("flags") or {}
    out: list[dict[str, Any]] = []
    for name in REQUIRED:
        if not roles.get(name):
            out.append(
                {
                    "check": "role_unmapped",
                    "level": "blocking",
                    "role": name,
                    "message": f"Choose the column for {BY_ID[name].label.lower()}.",
                }
            )
    for name, kind in UNIT_ROLES.items():
        if not roles.get(name):
            continue
        unit = units.get(name)
        if unit is None:
            out.append(
                {
                    "check": "units_undeclared",
                    "level": "blocking",
                    "role": name,
                    "message": f"Say what unit {BY_ID[name].label.lower()} is in: a unit column "
                    "or one value for every row. Units are never guessed.",
                }
            )
        elif "value" in unit and normalise(unit["value"], kind) is None:
            out.append(
                {
                    "check": "units_undeclared",
                    "level": "blocking",
                    "role": name,
                    "message": f"{unit['value']!r} is not a {kind} unit the launcher knows.",
                }
            )
    for name in FLAG_ROLES:
        if roles.get(name) and not (flags.get(name) or {}).get("table"):
            out.append(
                {
                    "check": "flag_table_missing",
                    "level": "blocking",
                    "role": name,
                    "message": f"Say which values of {roles[name]} mean TRUE and which FALSE.",
                }
            )
    for name, unit_role in (("CONC", "CONC_UNIT"), ("DOSE", "DOSE_UNIT")):
        column = (units.get(name) or {}).get("column")
        if column and roles.get(unit_role) and roles[unit_role] != column:
            out.append(
                {
                    "check": "column_reused",
                    "level": "blocking",
                    "role": unit_role,
                    "message": f"The {BY_ID[name].label.lower()} unit is read from {column}, but "
                    f"{roles[unit_role]} is mapped as its unit column.",
                }
            )
    used: dict[str, str] = {}
    for name, column in roles.items():
        if not column:
            continue
        if column in used:
            out.append(
                {
                    "check": "column_reused",
                    "level": "blocking",
                    "role": name,
                    "message": f"{column} is already the {BY_ID[used[column]].label.lower()}.",
                }
            )
        used[column] = name
    if events:
        from opncafyr.workflows.nca.events import mapping_problems

        out.extend(mapping_problems(mapping, events))
    return out
