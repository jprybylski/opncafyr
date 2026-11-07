"""Units (#23, ADR 0005 Q2): spelling normalisation only, never inference.

A unit comes from a per-row column or an explicit declaration. Normalisation maps
spelling variants to one form through a fixed table (``µ``/``μ`` -> ``u``, ``mcg`` ->
``ug``, ``ml`` -> ``mL``); anything else is unknown and the user must say what it is. A
unit written in a column header is only a hint to confirm.
"""

from __future__ import annotations

import re

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
