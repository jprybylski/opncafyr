"""Dosing routes as opnca-r accepts them (#23).

opnca-r (``R/opnca.R``) lower-cases a route, drops ``-``, ``_`` and spaces, and accepts only
the spellings below; anything else is an error there. The launcher never maps a route
by guesswork: values outside this list (``SC``, ``IM``, ``INTRAVENOUS``, typos) are listed
for the user to recode explicitly with a recipe step.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable

CANONICAL = {
    "iv-bolus": ("iv-bolus", "iv", "intravenous-bolus", "bolus"),
    "iv-infusion": ("iv-infusion", "infusion", "intravenous-infusion"),
    "extravascular": ("extravascular", "ev", "po", "oral"),
}
_LOOKUP = {
    re.sub(r"[-_ ]", "", alias): route for route, aliases in CANONICAL.items() for alias in aliases
}


def canonical_route(value: str) -> str | None:
    """opnca-r's route for ``value``, or None if opnca-r would refuse it."""
    return _LOOKUP.get(re.sub(r"[-_ ]", "", value.lower()))


def route_values(values: Iterable[str]) -> list[dict]:
    """Each distinct raw route value with its row count and opnca-r route (None = unknown)."""
    counts = Counter(values)
    return [
        {"value": v, "rows": n, "route": canonical_route(v) if v.strip() else None}
        for v, n in sorted(counts.items())
    ]
