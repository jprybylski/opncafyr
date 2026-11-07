"""Column-role suggestions (#23), ported from makecoolplots ``crates/mcp-inspect/src/roles.rs``.

A suggestion is ``{role, column, confidence, reasons}``. It is never applied by itself:
the page shows it as "suggested" and the user confirms it. A role whose best candidate is
below ``THRESHOLD``, or that two columns fit equally well, comes back as ``needs_choice``
with the candidates as options (makecoolplots ``mcp-core/src/error.rs``), so nothing is
picked silently. A required role with no candidate at all is a ``needs_choice`` over every
column.

Scoring follows makecoolplots: an exact alias scores 0.75, an alias found as a word inside
the name 0.40 (at most 0.55, so always below the threshold), and value checks add or take
away confidence with a reason each. Nominal-time names are never actual-time aliases.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any

from opncafyr.workflows.nca.recipe import INTEGER_RE, MISSING_TOKENS, NUMBER_RE, clean_name
from opncafyr.workflows.nca.routes import canonical_route
from opncafyr.workflows.nca.units import normalise

THRESHOLD = 0.6
EXACT, TOKEN, TOKEN_MAX = 0.75, 0.40, 0.55
MAX_CATEGORIES = 30


@dataclass(frozen=True)
class Role:
    id: str
    label: str
    aliases: tuple[str, ...]
    required: bool = False
    check: str = ""


# Order matters for ties between roles: earlier roles claim a column first.
ROLES: tuple[Role, ...] = (
    Role(
        "SUBJECT_ID",
        "Subject (profile) ID",
        ("usubjid", "id", "subject", "subject_id", "subj_id", "patient"),
        True,
        "id",
    ),
    Role("SECONDARY_ID", "Secondary subject ID", ("subjid", "subj", "ptno", "patno")),
    Role(
        "ANALYTE",
        "Analyte code",
        ("pctestcd", "paramcd", "analyte", "dvid", "cmt"),
        True,
        "category",
    ),
    Role(
        "ANALYTE_LABEL",
        "Analyte label",
        ("pctest", "param", "analyte_name", "dvname"),
        check="category",
    ),
    Role("SPECIMEN", "Specimen", ("pcspec", "specimen", "spec", "matrix"), check="category"),
    Role(
        "ACTUAL_TIME",
        "Actual time",
        ("afrlt", "actual_time", "arrlt", "time", "tafd", "atime", "tsfd", "tad", "pceltm", "eltm"),
        True,
        "time",
    ),
    Role(
        "NOMINAL_TIME",
        "Nominal time",
        (
            "nfrlt",
            "nrrlt",
            "nominal_time",
            "ntim",
            "nomtime",
            "ntpd",
            "ntafd",
            "nom_time",
            "ntime",
            "pctptnum",
        ),
        check="number",
    ),
    Role(
        "CONC",
        "Concentration",
        ("conc", "aval", "pcstresn", "dv", "lidv", "cobs", "concentration"),
        True,
        "conc",
    ),
    Role(
        "CONC_UNIT",
        "Concentration unit",
        ("pcstresu", "avalu", "concu", "conc_unit", "dvunit", "dvu"),
        check="conc_unit",
    ),
    Role("LLOQ", "LLOQ", ("lloq", "pclloq", "lloq_val", "lloq_value"), check="number"),
    Role("BLQ", "BLQ flag", ("blqfl", "blq", "bloq", "blqf", "cens"), check="flag"),
    Role("MISSING", "Missing flag", ("missfl", "missing", "mdv", "pcstat"), check="flag"),
    Role("EXCLUDED", "Exclusion flag", ("exclfl", "excluded", "exclude", "excl"), check="flag"),
    Role(
        "EXCLUSION_REASON",
        "Exclusion reason",
        ("exclreas", "exclusion_reason", "excl_reason", "reason", "pcreasnd"),
    ),
    Role("DOSE", "Dose", ("dose", "exdose", "amt", "doseamt"), True, "number"),
    Role(
        "DOSE_UNIT",
        "Dose unit",
        ("doseu", "exdosu", "dose_unit", "amtu", "amtunit"),
        check="amount_unit",
    ),
    Role("ROUTE", "Route", ("route", "exroute", "route_name"), True, "route"),
    Role("TAU", "Dosing interval (tau)", ("tau", "ii"), check="number"),
    Role(
        "INFUSION_DURATION",
        "Infusion duration",
        ("inf_dur", "infusion_duration", "dur", "tinf"),
        check="number",
    ),
    Role("OCCASION", "Occasion or period", ("occ", "occasion", "period", "aperiod", "visit")),
)
ROLE_IDS = tuple(r.id for r in ROLES)
REQUIRED = tuple(r.id for r in ROLES if r.required)
BY_ID = {r.id: r for r in ROLES}


@dataclass
class Column:
    """What the value checks need to know about one column."""

    name: str
    values: list[str]

    @property
    def present(self) -> list[str]:
        return [v for v in self.values if v.strip() not in MISSING_TOKENS]

    def share(self, pattern) -> float:
        present = self.present
        if not present:
            return 0.0
        return sum(1 for v in present if pattern.match(v)) / len(present)


def columns_from_rows(header: list[str], rows: list[list[str]]) -> list[Column]:
    return [
        Column(name, [r[i] if i < len(r) else "" for r in rows]) for i, name in enumerate(header)
    ]


def _name_score(role: Role, name: str) -> tuple[float, str] | None:
    clean = clean_name(name)
    if clean in role.aliases:
        return EXACT, f"The name {name} is a usual name for {role.label.lower()}."
    padded = f"_{clean}_"
    for alias in role.aliases:
        if len(alias) > 2 and f"_{alias}_" in padded:
            return TOKEN, f"The name {name} contains {alias.upper()}."
    return None


def _value_deltas(role: Role, col: Column, subject: Column | None) -> list[tuple[float, str]]:
    out: list[tuple[float, str]] = []
    present = col.present
    numeric = col.share(NUMBER_RE)
    distinct = set(present)
    kind = role.check
    if kind == "id":
        if len(present) > 1 and len(distinct) < len(present):
            out.append((0.15, "Values repeat, as a subject ID does across samples."))
        elif len(present) > 1:
            out.append((-0.30, "Every value is different, so it doesn't group samples."))
        if len(present) < len(col.values):
            out.append((-0.10, "Some values are blank."))
    elif kind in ("time", "number", "conc"):
        if numeric >= 0.9:
            out.append((0.05 if kind != "conc" else 0.10, "Values are numbers."))
        else:
            out.append((-0.40 if kind == "time" else -0.30, "Many values are not numbers."))
        if kind == "time" and numeric >= 0.9 and subject is not None:
            share = _non_decreasing(col, subject)
            if share is not None:
                if share >= 0.9:
                    out.append((0.15, "Times mostly increase within each subject."))
                else:
                    out.append((-0.20, "Times often decrease within a subject."))
    elif kind == "category":
        if len(distinct) > MAX_CATEGORIES:
            out.append((-0.40, f"More than {MAX_CATEGORIES} different values."))
        elif distinct:
            out.append((0.10, f"{len(distinct)} different values."))
    elif kind == "flag":
        if 0 < len(set(col.values)) <= 3:
            out.append((0.10, "Two or three different values, as a flag has."))
        elif len(set(col.values)) > 3:
            out.append((-0.30, "More values than a flag has."))
    elif kind in ("conc_unit", "amount_unit"):
        unit_kind = "conc" if kind == "conc_unit" else "amount"
        if distinct and all(normalise(v, unit_kind) for v in distinct):
            out.append((0.10, "Every value is a unit."))
        elif distinct:
            out.append((-0.30, "Some values are not units the launcher knows."))
    elif kind == "route":
        known = [v for v in distinct if canonical_route(v)]
        if distinct and len(known) * 2 >= len(distinct):
            out.append((0.10, "Values are dosing routes."))
        elif distinct:
            out.append((-0.20, "Few values are routes opnca-r knows."))
    return out


def _non_decreasing(time: Column, subject: Column) -> float | None:
    steps = good = 0
    last: dict[str, float] = {}
    for sid, t in zip(subject.values, time.values, strict=True):
        if not NUMBER_RE.match(t):
            continue
        value = float(t)
        if sid in last:
            steps += 1
            good += value >= last[sid]
        last[sid] = value
    return good / steps if steps else None


def _round(x: float) -> float:
    return round(min(max(x, 0.05), 0.99), 2)


def score(role: Role, col: Column, subject: Column | None = None) -> dict[str, Any] | None:
    named = _name_score(role, col.name)
    if named is None:
        return None
    base, reason = named
    deltas = _value_deltas(role, col, subject)
    conf = base + sum(d for d, _ in deltas)
    conf = min(conf, TOKEN_MAX) if base == TOKEN else conf
    return {
        "role": role.id,
        "column": col.name,
        "confidence": _round(conf),
        "reasons": [reason, *(r for _, r in deltas)],
    }


def suggest(columns: list[Column]) -> dict[str, Any]:
    """Suggestions and questions for every role. Nothing here is a decision."""
    by_name = {c.name: c for c in columns}
    subject = None
    candidates: dict[str, list[dict[str, Any]]] = {}
    for role in ROLES:
        found = []
        for col in columns:
            if col.name.startswith("opncafyr_"):
                continue
            s = score(role, col, subject)
            if s is not None:
                found.append(s)
        found.sort(key=lambda s: -s["confidence"])
        candidates[role.id] = found
        if role.id == "SUBJECT_ID" and found and found[0]["confidence"] >= THRESHOLD:
            subject = by_name[found[0]["column"]]
    suggestions: list[dict[str, Any]] = []
    questions: list[dict[str, Any]] = []
    taken: dict[str, str] = {}
    for role in ROLES:
        found = candidates[role.id]
        best = found[0] if found else None
        # Two columns above the threshold (AFRLT and ARRLT, say) is a question, not a pick.
        tie = len(found) > 1 and found[1]["confidence"] >= THRESHOLD
        clash = best is not None and best["column"] in taken
        if best is not None and best["confidence"] >= THRESHOLD and not tie and not clash:
            suggestions.append(best)
            taken[best["column"]] = role.id
            continue
        if not found and not role.required:
            continue
        options = found or [
            {"column": c.name, "confidence": None, "reasons": []}
            for c in columns
            if not c.name.startswith("opncafyr_")
        ]
        why = (
            "More than one column fits."
            if tie
            else f"{best['column']} already looks like {BY_ID[taken[best['column']]].label}."
            if clash
            else "No column looks right with enough confidence."
            if found
            else "No column has a usual name for it."
        )
        questions.append(
            {
                "code": "needs_choice",
                "role": role.id,
                "question": f"Which column holds the {role.label.lower()}?",
                "message": why,
                "choices": [
                    {
                        "value": o["column"],
                        "confidence": o["confidence"],
                        "detail": "; ".join(o["reasons"]),
                    }
                    for o in options
                ],
            }
        )
    return {"suggestions": suggestions, "needs_choice": questions}


def distinct_values(col: Column, cap: int = 50) -> dict[str, Any]:
    counts = Counter(col.values)
    items = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return {
        "column": col.name,
        "values": [{"value": v, "rows": n} for v, n in items[:cap]],
        "too_many": len(items) > cap,
        "numeric_share": col.share(NUMBER_RE),
        "integer_share": col.share(INTEGER_RE),
    }
