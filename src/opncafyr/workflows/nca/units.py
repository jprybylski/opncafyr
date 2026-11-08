"""Units (#23, ADR 0005 Q2): spelling normalisation only, never inference.

A unit comes from a per-row column or an explicit declaration. Normalisation maps
spelling variants to one form through a fixed table (``µ``/``μ`` -> ``u``, ``mcg`` ->
``ug``, ``ml`` -> ``mL``); anything else is unknown and the user must say what it is. A
unit written in a column header is only a hint to confirm.

The normalised units of the frozen input are passed to opnca-r as its unit labels
(``analysis_labels``, #57), so results are labelled in the units of the numbers. Only the
fixed table's units are passed: a unit outside it is blocked, never forwarded as text.
"""

from __future__ import annotations

import re
from typing import Any

CONC_UNITS = ("pg/mL", "ng/mL", "ug/mL", "mg/mL", "ng/L", "ug/L", "mg/L", "g/L")
AMOUNT_UNITS = ("ng", "ug", "mg", "g")
TIME_UNITS = {
    "s": ("s", "sec", "secs", "second", "seconds"),
    "min": ("min", "mins", "minute", "minutes"),
    "h": ("h", "hr", "hrs", "hour", "hours"),
    "d": ("d", "day", "days"),
    "w": ("w", "wk", "wks", "week", "weeks"),
}
KINDS = {"conc": CONC_UNITS, "amount": AMOUNT_UNITS, "time": tuple(TIME_UNITS)}
UNIT_ROLES = {"ACTUAL_TIME": "time", "CONC": "conc", "DOSE": "amount"}

_TIME = {alias: unit for unit, aliases in TIME_UNITS.items() for alias in aliases}
_SPELLING = {u.lower(): u for u in CONC_UNITS + AMOUNT_UNITS}


def _key(text: str) -> str:
    return text.strip().replace("µ", "u").replace("μ", "u").lower().replace("mcg", "ug")


def normalise(text: str, kind: str) -> str | None:
    """The one spelling of ``text`` as a ``kind`` unit, or None if it isn't one we know."""
    key = _key(text)
    if kind == "time":
        return _TIME.get(key)
    unit = _SPELLING.get(key)
    return unit if unit is not None and unit in KINDS[kind] else None


_HEADER_UNIT = re.compile(r"[\(\[]\s*([^\)\]]+?)\s*[\)\]]\s*$")


def header_hint(column: str, kind: str) -> str | None:
    """A unit written in a header, such as ``CONC (ng/mL)``. Only a hint to confirm."""
    match = _HEADER_UNIT.search(column)
    return normalise(match.group(1), kind) if match else None


# opnca-r's name for each unit role (opnca_units(), opnca-r#32/#56), in its order.
LABELS = {"CONC": "concentration", "ACTUAL_TIME": "time", "DOSE": "dose"}


class UnitLabelError(ValueError):
    def __init__(self, role: str, message: str) -> None:
        super().__init__(message)
        self.role = role


def analysis_labels(units: dict[str, Any], rows: list[dict[str, str]]) -> dict[str, str]:
    """The unit labels of the frozen analysis input, for opnca-r (#57).

    Each role's unit is its declared value, or the one unit its unit column holds across
    ``rows`` (the cleaned, one-analyte rows that go to opnca-r), in the spelling of
    ``normalise``. Blank unit cells are skipped. A unit the table doesn't know, or more
    than one, is an error: the checks block those before a run is made, and nothing
    here picks one. Labels are metadata; nothing is converted.
    """
    labels: dict[str, str] = {}
    for role, name in LABELS.items():
        kind = UNIT_ROLES[role]
        unit = units.get(role) or {}
        if "value" in unit:
            raw = {unit["value"]}
        elif "column" in unit:
            raw = {(r.get(unit["column"]) or "").strip() for r in rows} - {""}
        else:
            raise UnitLabelError(role, f"No {name} unit is declared.")
        seen = {normalise(v, kind) for v in raw}
        if None in seen:
            raise UnitLabelError(role, f"The {name} unit is not one the launcher knows.")
        if len(seen) != 1:
            found = "no" if not seen else "more than one"
            raise UnitLabelError(role, f"The analysis input has {found} {name} unit.")
        labels[name] = seen.pop()  # type: ignore[assignment]
    return labels
