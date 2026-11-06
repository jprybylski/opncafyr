"""Generate the two-analyte scientific test fixture (#20).

Usage (from the repository root):

    uv run python tests/fixtures/make_fixtures.py [--out DIR]

DIR defaults to this script's folder. The script writes:

- ``two_analyte.csv``: two analytes (PARENT, METAB) on one shared subject x time grid,
  with one planted case for each data-level blocking and warning check of ADR 0005 Q7;
- ``two_analyte_mixed_units.csv``: the same rows with mixed PARENT concentration units;
- ``two_analyte.expected.json``: row counts, every planted case with its line numbers,
  the findings each check must report under the standard setup, and mapping scenarios.

The output is deterministic (fixed seed, fixed number formats, ``\\n`` line endings), so
running the script twice gives byte-identical files. ``tests/test_fixtures.py`` fails if
the committed files differ from a fresh run. Never hand-edit the outputs; change this
script and regenerate. Every subject is synthetic. See ``README.md`` for the cases.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

SEED = 20261106
STUDYID = "SYNTH-TA"
SUBJECTS = [f"TA-{i:03d}" for i in range(1, 11)]
NOMINAL_H = [0, 0.5, 1, 2, 4, 8, 12, 24]
ANALYTES = ("PARENT", "METAB")
LABELS = {"PARENT": "Parent drug", "METAB": "Metabolite M1"}
LLOQ = {"PARENT": "0.05", "METAB": "0.02"}
URINE_LLOQ = "0.5"
DOSE_MG = 100
ROUTE = "extravascular"

MAIN = "two_analyte.csv"
VARIANT = "two_analyte_mixed_units.csv"
EXPECTED = "two_analyte.expected.json"

COLUMNS = [
    "STUDYID",
    "USUBJID",
    "SUBJID",
    "PCTESTCD",
    "PCTEST",
    "PCSPEC",
    "AFRLT",
    "NFRLT",
    "PCDTC",
    "EXSTDTC",
    "PCTM",
    "CONC",
    "PCSTRESU",
    "BLQFL",
    "LLOQ",
    "MISSFL",
    "EXCLFL",
    "EXCLREAS",
    "DOSE",
    "DOSEU",
    "ROUTE",
]

# ADR 0005 Q7. Info checks are listed only where they are planted.
LEVELS = {
    "role_unmapped": "blocking",
    "analyte_no_rows": "blocking",
    "specimen_mixed": "blocking",
    "time_not_numeric": "blocking",
    "conc_not_numeric": "blocking",
    "conc_unclassified_blank": "blocking",
    "conc_negative": "blocking",
    "route_missing": "blocking",
    "route_unrecognised": "blocking",
    "route_varies": "blocking",
    "dose_varies": "blocking",
    "units_mixed": "blocking",
    "units_undeclared": "blocking",
    "duplicate_key": "blocking",
    "blq_and_missing": "blocking",
    "flag_value_unmapped": "blocking",
    "exclusion_no_reason": "blocking",
    "time_nominal_as_actual": "blocking",
    "time_clock_format": "blocking",
    "time_derivation_failed": "blocking",
    "profile_no_dose": "blocking",
    "conc_zero_not_blq": "warning",
    "blq_above_lloq": "warning",
    "lloq_varies": "warning",
    "time_negative": "warning",
    "id_usubjid_not_one_to_one": "warning",
    "session_exclusion_no_reason": "warning",
    "time_not_sorted": "info",
    "too_few_points": "info",
}


@dataclass
class Row:
    values: dict[str, str]
    subject: int
    minutes: int
    seq: int
    tags: list[str] = field(default_factory=list)

    @property
    def analyte(self) -> str:
        return self.values["PCTESTCD"]

    @property
    def nominal(self) -> float:
        return float(self.values["NFRLT"])

    def sort_key(self) -> tuple[int, int, int, int, int]:
        analyte = ANALYTES.index(self.analyte.strip())
        specimen = 0 if self.values["PCSPEC"] == "PLASMA" else 1
        return (self.subject, self.minutes, specimen, analyte, self.seq)


def _fmt_conc(value: float) -> str:
    return f"{value:.3f}"


def _fmt_hours(minutes: int) -> str:
    return f"{minutes / 60:.4f}"


def _fmt_nominal(hours: float) -> str:
    return f"{hours:g}"


def _parent_conc(t: float, ka: float, ke: float, v: float) -> float:
    """One-compartment oral model, ng/mL (dose mg / volume L = mg/L = 1000 ng/mL)."""
    return 1000 * DOSE_MG / v * ka / (ka - ke) * (math.exp(-ke * t) - math.exp(-ka * t))


def _metab_conc(t: float, ka: float, ke: float, kem: float, vm: float, fm: float) -> float:
    """Metabolite formed from the parent (fraction fm), first-order elimination kem."""
    scale = 1000 * fm * DOSE_MG * ka * ke / vm
    return scale * (
        math.exp(-ka * t) / ((ke - ka) * (kem - ka))
        + math.exp(-ke * t) / ((ka - ke) * (kem - ke))
        + math.exp(-kem * t) / ((ka - kem) * (ke - kem))
    )


class Builder:
    def __init__(self) -> None:
        self.rng = random.Random(SEED)  # noqa: S311 - reproducible synthetic data
        self.rows: list[Row] = []
        self.seq = 0
        self.dose_times: dict[str, datetime] = {}

    # --- base grid ----------------------------------------------------------------------

    def base(self) -> None:
        for i, usubjid in enumerate(SUBJECTS):
            ka = 1.2 * self.rng.lognormvariate(0, 0.2)
            ke = 0.12 * self.rng.lognormvariate(0, 0.15)
            v = 400 * self.rng.lognormvariate(0, 0.2)
            kem = 0.3 * self.rng.lognormvariate(0, 0.1)
            vm = 200 * self.rng.lognormvariate(0, 0.2)
            fm = 0.4
            dose_time = datetime(2026, 1, 5 + i, 8, 0)
            self.dose_times[usubjid] = dose_time
            for t in NOMINAL_H:
                jitter = 0 if t == 0 else self.rng.randint(-5, 5)
                minutes = round(t * 60) + jitter
                hours = minutes / 60
                concs = {
                    "PARENT": _parent_conc(hours, ka, ke, v),
                    "METAB": _metab_conc(hours, ka, ke, kem, vm, fm),
                }
                for analyte in ANALYTES:
                    noise = self.rng.lognormvariate(0, 0.08)
                    conc = concs[analyte] * noise
                    if t == 0:
                        text = ""
                    else:
                        if conc < 1.0:
                            raise AssertionError(f"base conc too low: {usubjid} {analyte} {t}")
                        text = _fmt_conc(conc)
                    self.add(
                        i,
                        minutes,
                        self._values(usubjid, i, analyte, "PLASMA", minutes, t, text),
                    )

    def _values(
        self,
        usubjid: str,
        index: int,
        analyte: str,
        specimen: str,
        minutes: int,
        nominal: float,
        conc: str,
    ) -> dict[str, str]:
        dose_time = self.dose_times[usubjid]
        sampled = dose_time + timedelta(minutes=minutes)
        return {
            "STUDYID": STUDYID,
            "USUBJID": usubjid,
            "SUBJID": f"{index + 1:03d}",
            "PCTESTCD": analyte,
            "PCTEST": LABELS[analyte],
            "PCSPEC": specimen,
            "AFRLT": _fmt_hours(minutes),
            "NFRLT": _fmt_nominal(nominal),
            "PCDTC": sampled.strftime("%Y-%m-%dT%H:%M"),
            "EXSTDTC": dose_time.strftime("%Y-%m-%dT%H:%M"),
            "PCTM": sampled.strftime("%H:%M"),
            "CONC": conc,
            "PCSTRESU": "ng/mL",
            "BLQFL": "Y" if nominal == 0 else "",
            "LLOQ": LLOQ[analyte] if specimen == "PLASMA" else URINE_LLOQ,
            "MISSFL": "",
            "EXCLFL": "",
            "EXCLREAS": "",
            "DOSE": str(DOSE_MG),
            "DOSEU": "mg",
            "ROUTE": ROUTE,
        }

    def add(self, subject: int, minutes: int, values: dict[str, str], tag: str = "") -> Row:
        self.seq += 1
        row = Row(values=values, subject=subject, minutes=minutes, seq=self.seq)
        if tag:
            row.tags.append(tag)
        self.rows.append(row)
        return row

    # --- selection ----------------------------------------------------------------------

    def select(
        self,
        usubjid: str,
        analyte: str | None = None,
        nominal: float | None = None,
        specimen: str = "PLASMA",
    ) -> list[Row]:
        out = [
            r
            for r in self.rows
            if r.values["USUBJID"] == usubjid
            and r.values["PCSPEC"] == specimen
            and (analyte is None or r.analyte == analyte)
            and (nominal is None or r.nominal == nominal)
        ]
        if not out:
            raise AssertionError(f"nothing selected: {usubjid} {analyte} {nominal}")
        return out

    def one(self, usubjid: str, analyte: str, nominal: float) -> Row:
        (row,) = self.select(usubjid, analyte, nominal)
        return row

    def tag(self, rows: list[Row], tag: str, **changes: str) -> None:
        for row in rows:
            row.values.update(changes)
            row.tags.append(tag)

    # --- planted cases ------------------------------------------------------------------

    def plant(self) -> None:
        predose = [r for r in self.rows if r.nominal == 0]
        self.tag(predose, "blq_flag_lloq")
        for s in ("TA-009", "TA-010"):
            self.tag([self.one(s, "METAB", 24)], "blq_flag_lloq", CONC="", BLQFL="Y")

        # TA-001
        self.tag([self.one("TA-001", "PARENT", 12)], "conc_unclassified_blank", CONC="")
        self.tag([self.one("TA-001", "METAB", 8)], "flag_value_unmapped", BLQFL="N")
        self.tag(
            [self.one("TA-001", "METAB", 2)],
            "exclusion_with_reason",
            EXCLFL="Y",
            EXCLREAS="Haemolysed sample",
        )
        # TA-002
        self.tag(self.select("TA-002", "PARENT"), "lloq_varies", LLOQ="0.10")
        self.tag([self.one("TA-002", "PARENT", 1)], "exclusion_no_reason", EXCLFL="Y")
        self.tag([self.one("TA-002", "METAB", 12)], "route_missing", ROUTE="")
        # TA-001 and TA-002: urine collections off the plasma grid.
        for s in ("TA-001", "TA-002"):
            index = SUBJECTS.index(s)
            plasma_2h = float(self.one(s, "PARENT", 2).values["CONC"])
            for nominal, factor in ((3, 8.0), (6, 5.0)):
                minutes = nominal * 60
                values = self._values(
                    s, index, "PARENT", "URINE", minutes, nominal, _fmt_conc(plasma_2h * factor)
                )
                self.add(index, minutes, values, "second_specimen")
        # TA-003: early predose sample and text BLQ for METAB.
        index = SUBJECTS.index("TA-003")
        dose_time = self.dose_times["TA-003"]
        for row in self.select("TA-003", nominal=0):
            sampled = dose_time - timedelta(minutes=15)
            row.minutes = -15
            row.values.update(
                AFRLT=_fmt_hours(-15),
                PCDTC=sampled.strftime("%Y-%m-%dT%H:%M"),
                PCTM=sampled.strftime("%H:%M"),
            )
            row.tags.append("time_negative")
        text_blq = [r for r in self.select("TA-003", "METAB") if r.nominal >= 2]
        self.tag(text_blq, "blq_text", CONC="<LLOQ")
        self.tag(self.select("TA-003", "METAB"), "too_few_points")
        # TA-004
        self.tag([self.one("TA-004", "PARENT", 24)], "conc_negative", CONC="-0.020")
        self.tag([self.one("TA-004", "METAB", 12)], "missing_sample", CONC="", MISSFL="Y")
        self.tag([self.one("TA-004", "PARENT", 4)], "subjid_typo", SUBJID="040")
        # TA-005
        original = self.one("TA-005", "PARENT", 2)
        original.tags.append("duplicate_exact")
        copy = self.add(original.subject, original.minutes, dict(original.values))
        copy.tags.append("duplicate_exact")
        self.tag([self.one("TA-005", "METAB", 8)], "time_not_numeric", AFRLT="~8")
        self.tag([self.one("TA-005", "METAB", 24)], "conc_zero_not_blq", CONC="0")
        self.tag(
            [self.one("TA-005", "PARENT", 0.5)],
            "exclusion_with_reason",
            EXCLFL="Y",
            EXCLREAS="Sample collected outside the window",
        )
        # TA-006
        original = self.one("TA-006", "METAB", 4)
        original.tags.append("duplicate_conflicting")
        values = dict(original.values, CONC=_fmt_conc(float(original.values["CONC"]) * 0.96))
        self.add(original.subject, original.minutes, values, "duplicate_conflicting")
        date_only = self.dose_times["TA-006"].strftime("%Y-%m-%d")
        self.tag(self.select("TA-006"), "partial_dose_date", EXSTDTC=date_only)
        self.tag([self.one("TA-006", "PARENT", 24)], "blq_above_lloq", CONC="0.080", BLQFL="Y")
        # TA-007: one deliberately invalid extra sample, and a BLQ row also marked missing.
        index = SUBJECTS.index("TA-007")
        values = self._values("TA-007", index, "PARENT", "PLASMA", 365, 6, "12,4")
        values["ROUTE"] = "EXTRAVASULAR"
        self.add(index, 365, values, "invalid_row")
        row = self.one("TA-007", "METAB", 24)
        self.tag([row], "blq_and_missing", CONC="", BLQFL="Y", MISSFL="Y")
        # TA-008: no dose record.
        self.tag(self.select("TA-008"), "no_dose", DOSE="", DOSEU="")
        # TA-009: a different dose on one PARENT row, and the analyte code with a trailing
        # space.
        self.tag([self.one("TA-009", "PARENT", 12)], "dose_varies", DOSE="200")
        source = self.one("TA-009", "METAB", 4)
        values = dict(source.values, PCTESTCD="METAB ")
        self.add(source.subject, source.minutes, values, "analyte_value_variant")

    def ordered(self) -> list[Row]:
        rows = sorted(self.rows, key=Row.sort_key)
        # TA-004: write the 24 h samples before the 12 h samples (both analytes).
        index = SUBJECTS.index("TA-004")
        at12 = [i for i, r in enumerate(rows) if r.subject == index and r.nominal == 12]
        at24 = [i for i, r in enumerate(rows) if r.subject == index and r.nominal == 24]
        if at12[-1] + 1 != at24[0]:
            raise AssertionError("TA-004 blocks are not adjacent")
        block12 = [rows[i] for i in at12]
        block24 = [rows[i] for i in at24]
        for r in block12 + block24:
            r.tags.append("time_not_sorted")
        rows[at12[0] : at24[-1] + 1] = block24 + block12
        # Tags are de-duplicated but keep their order.
        for r in rows:
            r.tags = list(dict.fromkeys(r.tags))
        return rows


# --- outputs --------------------------------------------------------------------------------


def _csv_bytes(rows: list[Row]) -> bytes:
    buf = io.StringIO(newline="")
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(COLUMNS)
    for r in rows:
        writer.writerow([r.values[c] for c in COLUMNS])
    return buf.getvalue().encode("utf-8")


def _variant(rows: list[Row]) -> list[Row]:
    out: list[Row] = []
    for r in rows:
        copy = Row(dict(r.values), r.subject, r.minutes, r.seq, list(r.tags))
        plasma_parent = copy.values["PCSPEC"] == "PLASMA" and copy.analyte == "PARENT"
        usubjid = copy.values["USUBJID"]
        if plasma_parent and usubjid in ("TA-001", "TA-002"):
            copy.values["PCSTRESU"] = "ug/L"
            copy.tags.append("mixed_units")
        elif plasma_parent and usubjid == "TA-003":
            copy.values["PCSTRESU"] = "ng/ml"
            copy.tags.append("unit_spelling")
        out.append(copy)
    return out


def _lines(rows: list[Row], tag: str, **where: str) -> list[int]:
    """Physical line numbers (the header is line 1) of rows carrying ``tag``."""
    return [
        i + 2
        for i, r in enumerate(rows)
        if tag in r.tags and all(r.values[k] == v for k, v in where.items())
    ]


def _profile_lines(rows: list[Row], usubjid: str, analyte: str) -> list[int]:
    return [
        i + 2
        for i, r in enumerate(rows)
        if r.values["USUBJID"] == usubjid
        and r.analyte == analyte
        and r.values["PCSPEC"] == "PLASMA"
    ]


CASES: list[dict[str, object]] = [
    {
        "id": "blq_flag_lloq",
        "description": "BLQ as a flag plus an LLOQ column: CONC blank, BLQFL Y (all predose "
        "samples, and METAB at 24 h for TA-009 and TA-010).",
        "values": {"BLQFL": "Y", "CONC": ""},
    },
    {
        "id": "blq_text",
        "description": "BLQ written as text: TA-003 METAB from 2 h has CONC '<LLOQ' and no "
        "flag. Only the text-pattern BLQ step classifies it.",
        "values": {"CONC": "<LLOQ", "BLQFL": ""},
    },
    {
        "id": "exclusion_with_reason",
        "description": "Excluded samples with a reason (EXCLFL Y, EXCLREAS filled).",
        "values": {"EXCLFL": "Y"},
    },
    {
        "id": "exclusion_no_reason",
        "description": "An excluded sample with no reason (TA-002 PARENT 1 h).",
        "values": {"EXCLFL": "Y", "EXCLREAS": ""},
    },
    {
        "id": "duplicate_exact",
        "description": "An exact copy of a row (TA-005 PARENT 2 h), on adjacent lines.",
        "values": {},
    },
    {
        "id": "duplicate_conflicting",
        "description": "Two rows with the same key and different CONC (TA-006 METAB 4 h).",
        "values": {},
    },
    {
        "id": "invalid_row",
        "description": "A deliberately invalid extra sample (TA-007 PARENT, nominal 6 h): "
        "CONC '12,4' (decimal comma) and ROUTE 'EXTRAVASULAR' (misspelt, unknown).",
        "values": {"CONC": "12,4", "ROUTE": "EXTRAVASULAR"},
    },
    {
        "id": "no_dose",
        "description": "A subject with no dose record (TA-008: DOSE and DOSEU blank). The "
        "profile is kept, never dropped.",
        "values": {"DOSE": ""},
    },
    {
        "id": "second_specimen",
        "description": "URINE rows for PARENT (TA-001, TA-002 at 3 h and 6 h, off the plasma "
        "grid). Filtered out by PCSPEC=PLASMA; counted, not rejected.",
        "values": {"PCSPEC": "URINE"},
    },
    {
        "id": "analyte_value_variant",
        "description": "An analyte code with a trailing space ('METAB '). The exact-value "
        "filter does not match it to METAB; it counts as another analyte.",
        "values": {"PCTESTCD": "METAB "},
    },
    {
        "id": "missing_sample",
        "description": "A sample not analysed: MISSFL Y, CONC blank (TA-004 METAB 12 h). "
        "Missing, not BLQ.",
        "values": {"MISSFL": "Y", "CONC": ""},
    },
    {
        "id": "time_not_numeric",
        "description": "Actual time written as '~8' (TA-005 METAB 8 h).",
        "values": {"AFRLT": "~8"},
    },
    {
        "id": "conc_unclassified_blank",
        "description": "A blank CONC with no BLQ or missing flag (TA-001 PARENT 12 h).",
        "values": {"CONC": "", "BLQFL": "", "MISSFL": ""},
    },
    {
        "id": "conc_negative",
        "description": "A negative concentration (TA-004 PARENT 24 h).",
        "values": {"CONC": "-0.020"},
    },
    {
        "id": "route_missing",
        "description": "A blank route on one row (TA-002 METAB 12 h).",
        "values": {"ROUTE": ""},
    },
    {
        "id": "blq_and_missing",
        "description": "A row flagged both BLQ and missing (TA-007 METAB 24 h).",
        "values": {"BLQFL": "Y", "MISSFL": "Y"},
    },
    {
        "id": "flag_value_unmapped",
        "description": "BLQFL 'N', outside the confirmed table {Y, blank} (TA-001 METAB 8 h).",
        "values": {"BLQFL": "N"},
    },
    {
        "id": "conc_zero_not_blq",
        "description": "A post-dose CONC of 0 with no BLQ flag (TA-005 METAB 24 h).",
        "values": {"CONC": "0", "BLQFL": ""},
    },
    {
        "id": "blq_above_lloq",
        "description": "A BLQ-flagged sample whose CONC (0.080) is above the LLOQ (0.05) "
        "(TA-006 PARENT 24 h).",
        "values": {"BLQFL": "Y", "CONC": "0.080"},
    },
    {
        "id": "lloq_varies",
        "description": "TA-002 PARENT plasma rows carry LLOQ 0.10; every other PARENT plasma "
        "row carries 0.05.",
        "values": {"LLOQ": "0.10"},
    },
    {
        "id": "time_negative",
        "description": "Predose samples taken 15 minutes before the dose (TA-003, both "
        "analytes, AFRLT -0.2500).",
        "values": {"AFRLT": "-0.2500"},
    },
    {
        "id": "time_not_sorted",
        "description": "TA-004's 24 h samples are written before its 12 h samples.",
        "values": {},
    },
    {
        "id": "too_few_points",
        "description": "TA-003 METAB has two measurable post-dose samples (0.5 h, 1 h) once "
        "the text BLQ rows are classified.",
        "values": {"USUBJID": "TA-003", "PCTESTCD": "METAB"},
    },
    {
        "id": "dose_varies",
        "description": "TA-009 PARENT 12 h records DOSE 200; the rest of the profile says 100.",
        "values": {"DOSE": "200"},
    },
    {
        "id": "subjid_typo",
        "description": "SUBJID '040' on one TA-004 row (all its other rows say '004').",
        "values": {"SUBJID": "040"},
    },
    {
        "id": "partial_dose_date",
        "description": "TA-006 EXSTDTC is a date with no time, so elapsed time can't be "
        "derived from PCDTC - EXSTDTC.",
        "values": {"EXSTDTC": "2026-01-10"},
    },
    {
        "id": "mixed_units",
        "file": VARIANT,
        "description": "Variant only: TA-001 and TA-002 PARENT plasma rows in ug/L, the rest "
        "in ng/mL. Equivalent units, still blocking.",
        "values": {"PCSTRESU": "ug/L"},
    },
    {
        "id": "unit_spelling",
        "description": "Variant only: TA-003 PARENT plasma rows in 'ng/ml'. The spelling "
        "table normalises this to ng/mL; not a finding on its own.",
        "file": VARIANT,
        "values": {"PCSTRESU": "ng/ml"},
    },
]


def _finding(
    check: str,
    analyte: str,
    scope: str,
    lines: list[int],
    subject: str | None = None,
    **extra: str,
) -> dict[str, object]:
    if not lines:
        raise AssertionError(f"finding {check} has no lines")
    out: dict[str, object] = {
        "check": check,
        "level": LEVELS[check],
        "analyte": analyte,
        "scope": scope,
        "subject": subject,
        "lines": lines,
    }
    out.update(extra)
    return out


# (check, analyte, scope, subject, tag that marks the lines, extra fields). A tag of
# "profile" means every plasma row of that subject's profile.
FINDINGS: list[tuple[str, str, str, str | None, str, dict[str, str]]] = [
    ("conc_unclassified_blank", "PARENT", "row", "TA-001", "conc_unclassified_blank", {}),
    ("flag_value_unmapped", "METAB", "row", "TA-001", "flag_value_unmapped",
     {"column": "BLQFL", "value": "N"}),
    ("lloq_varies", "PARENT", "analyte", "TA-002", "lloq_varies", {"values": "0.05, 0.10"}),
    ("exclusion_no_reason", "PARENT", "row", "TA-002", "exclusion_no_reason", {}),
    ("route_missing", "METAB", "row", "TA-002", "route_missing", {}),
    ("time_negative", "PARENT", "row", "TA-003", "time_negative", {}),
    ("time_negative", "METAB", "row", "TA-003", "time_negative", {}),
    ("too_few_points", "METAB", "profile", "TA-003", "too_few_points", {}),
    ("conc_negative", "PARENT", "row", "TA-004", "conc_negative", {}),
    ("time_not_sorted", "PARENT", "profile", "TA-004", "time_not_sorted", {}),
    ("time_not_sorted", "METAB", "profile", "TA-004", "time_not_sorted", {}),
    ("duplicate_key", "PARENT", "key", "TA-005", "duplicate_exact", {"kind": "exact"}),
    ("time_not_numeric", "METAB", "row", "TA-005", "time_not_numeric", {}),
    ("conc_zero_not_blq", "METAB", "row", "TA-005", "conc_zero_not_blq", {}),
    ("duplicate_key", "METAB", "key", "TA-006", "duplicate_conflicting",
     {"kind": "conflicting"}),
    ("blq_above_lloq", "PARENT", "row", "TA-006", "blq_above_lloq", {}),
    ("conc_not_numeric", "PARENT", "row", "TA-007", "invalid_row", {}),
    ("route_unrecognised", "PARENT", "row", "TA-007", "invalid_row", {}),
    ("route_varies", "PARENT", "profile", "TA-007", "profile",
     {"values": "extravascular, EXTRAVASULAR"}),
    ("blq_and_missing", "METAB", "row", "TA-007", "blq_and_missing", {}),
    ("profile_no_dose", "PARENT", "profile", "TA-008", "no_dose", {}),
    ("profile_no_dose", "METAB", "profile", "TA-008", "no_dose", {}),
    ("dose_varies", "PARENT", "profile", "TA-009", "profile", {"values": "100, 200"}),
]  # fmt: skip
VARIANT_FINDINGS: list[tuple[str, str, str, str | None, str, dict[str, str]]] = [
    ("units_mixed", "PARENT", "analyte", None, "mixed_units", {"values": "ng/mL, ug/L"}),
]


def _findings(rows: list[Row], variant: bool) -> list[dict[str, object]]:
    table = FINDINGS + (VARIANT_FINDINGS if variant else [])
    out = []
    for check, analyte, scope, subject, tag, extra in table:
        if tag == "profile":
            assert subject is not None
            lines = _profile_lines(rows, subject, analyte)
        else:
            lines = _lines(rows, tag, PCTESTCD=analyte)
        out.append(_finding(check, analyte, scope, lines, subject, **extra))
    return out


def _scenarios(rows: list[Row]) -> list[dict[str, object]]:
    def profile(analyte: str, usubjid: str) -> list[int]:
        return _profile_lines(rows, usubjid, analyte)

    both = list(ANALYTES)
    return [
        {
            "id": "dose_unmapped",
            "analytes": both,
            "change": {"mapping": {"DOSE": None}},
            "includes": [{"check": "role_unmapped", "level": "blocking", "role": "DOSE"}],
        },
        {
            "id": "analyte_value_case",
            "analytes": ["Parent"],
            "change": {"analyte_value": "Parent"},
            "includes": [{"check": "analyte_no_rows", "level": "blocking"}],
        },
        {
            "id": "conc_unit_undeclared",
            "analytes": both,
            "change": {"units": {"CONC": None}},
            "includes": [{"check": "units_undeclared", "level": "blocking", "role": "CONC"}],
        },
        {
            "id": "specimen_unmapped",
            "change": {"mapping": {"SPECIMEN": None}},
            "analytes": ["PARENT"],
            "includes": [
                {
                    "check": "specimen_mixed",
                    "level": "blocking",
                    "values": "PLASMA, URINE",
                    "lines": _lines(rows, "second_specimen"),
                }
            ],
            "note": "METAB has only PLASMA rows, so it has no specimen_mixed finding.",
        },
        {
            "id": "nominal_as_actual",
            "analytes": both,
            "change": {"mapping": {"ACTUAL_TIME": "NFRLT"}},
            "includes": [{"check": "time_nominal_as_actual", "level": "blocking"}],
        },
        {
            "id": "clock_as_actual",
            "analytes": both,
            "change": {"mapping": {"ACTUAL_TIME": "PCTM"}},
            "includes": [{"check": "time_clock_format", "level": "blocking"}],
        },
        {
            "id": "derive_elapsed_time",
            "analytes": both,
            "change": {
                "recipe": [
                    {
                        "step": "derive_elapsed_time",
                        "from": "PCDTC",
                        "reference": "EXSTDTC",
                        "unit": "h",
                        "time_zone": "all local, none given",
                    }
                ]
            },
            "includes": [
                {
                    "check": "time_derivation_failed",
                    "level": "blocking",
                    "subject": "TA-006",
                    "analyte": analyte,
                    "lines": profile(analyte, "TA-006"),
                }
                for analyte in ANALYTES
            ],
            "note": "TA-006 EXSTDTC has no time. Every other row derives to its AFRLT.",
        },
        {
            "id": "secondary_id_mapped",
            "change": {"mapping": {"SECONDARY_ID": "SUBJID"}},
            "analytes": ["PARENT"],
            "includes": [
                {
                    "check": "id_usubjid_not_one_to_one",
                    "level": "warning",
                    "subject": "TA-004",
                    "lines": _lines(rows, "subjid_typo"),
                }
            ],
        },
        {
            "id": "analyte_code_trimmed",
            "change": {"recipe": [{"step": "trim", "columns": ["PCTESTCD"]}]},
            "analytes": ["METAB"],
            "includes": [
                {
                    "check": "duplicate_key",
                    "level": "blocking",
                    "kind": "exact",
                    "subject": "TA-009",
                    "lines": sorted(
                        [
                            *_lines(rows, "analyte_value_variant"),
                            *[n for n in profile("METAB", "TA-009") if rows[n - 2].nominal == 4],
                        ]
                    ),
                }
            ],
            "note": "Trimming makes the 'METAB ' row a METAB row with the same key as the "
            "TA-009 METAB 4 h sample. Only differences in PCTESTCD make it a copy.",
        },
    ]


def _analytes(rows: list[Row]) -> dict[str, object]:
    out: dict[str, object] = {}
    for code in sorted({r.analyte for r in rows}):
        mine = [r for r in rows if r.analyte == code]
        specimens = sorted({r.values["PCSPEC"] for r in mine})
        out[code] = {
            "rows": len(mine),
            "by_specimen": {s: sum(r.values["PCSPEC"] == s for r in mine) for s in specimens},
            "subjects": len({r.values["USUBJID"] for r in mine}),
        }
    keys = {
        a: {
            (r.values["USUBJID"], r.values["AFRLT"])
            for r in rows
            if r.analyte == a and r.values["PCSPEC"] == "PLASMA"
        }
        for a in ANALYTES
    }
    return {
        "column": "PCTESTCD",
        "values": out,
        "shared_plasma_keys": len(keys["PARENT"] & keys["METAB"]),
    }


STANDARD_SETUP = {
    "mapping": {
        "SUBJECT_ID": "USUBJID",
        "ANALYTE": "PCTESTCD",
        "ANALYTE_LABEL": "PCTEST",
        "SPECIMEN": "PCSPEC",
        "ACTUAL_TIME": "AFRLT",
        "NOMINAL_TIME": "NFRLT",
        "CONC": "CONC",
        "CONC_UNIT": "PCSTRESU",
        "LLOQ": "LLOQ",
        "BLQ": "BLQFL",
        "MISSING": "MISSFL",
        "EXCLUDED": "EXCLFL",
        "EXCLUSION_REASON": "EXCLREAS",
        "DOSE": "DOSE",
        "DOSE_UNIT": "DOSEU",
        "ROUTE": "ROUTE",
    },
    "specimen_value": "PLASMA",
    "analyte_values": list(ANALYTES),
    "blq": {
        "flag_column": "BLQFL",
        "flag_table": {"Y": True, "": False},
        "text_column": "CONC",
        "text_pattern": "^<",
    },
    "missing": {"column": "MISSFL", "table": {"Y": True, "": False}},
    "excluded": {"column": "EXCLFL", "table": {"Y": True, "": False}, "reason": "EXCLREAS"},
    "units": {"time": "h", "conc": "from PCSTRESU", "dose": "from DOSEU"},
    "recipe": [],
}

RULES = [
    "Line numbers are physical lines; the header is line 1. No field contains a newline.",
    "Findings are per analyte, after the exact-value analyte filter and the PCSPEC=PLASMA "
    "specimen filter.",
    "'findings' is complete for blocking and warning checks under standard_setup. Info "
    "checks are listed only where planted.",
    "Scenario 'includes' lists findings that must appear; others may appear too.",
    "route_varies and dose_varies count distinct non-blank values in a profile.",
    "duplicate_key uses USUBJID x ACTUAL_TIME within the analyte, over non-excluded rows "
    "(ADR 0005 Q5).",
    "A blank CONC is classified by BLQ or MISSING first; CONC text matching the BLQ pattern "
    "is BLQ, not conc_not_numeric (ADR 0005 Q3).",
    "lloq_varies is an analyte-level finding; its lines are the minority LLOQ rows.",
    "profile_no_dose fires once per profile; its lines are the whole profile.",
]


def generate() -> dict[str, bytes]:
    builder = Builder()
    builder.base()
    builder.plant()
    rows = builder.ordered()
    variant = _variant(rows)

    files = {}
    for name, data in ((MAIN, rows), (VARIANT, variant)):
        files[name] = {
            "rows": len(data),
            "columns": COLUMNS,
            "analytes": _analytes(data),
        }

    cases = []
    for case in CASES:
        name = str(case.get("file", MAIN))
        data = variant if name == VARIANT else rows
        lines = _lines(data, str(case["id"]))
        if not lines:
            raise AssertionError(f"case {case['id']} planted nothing")
        cases.append(
            {
                "id": case["id"],
                "files": [name] if name == VARIANT else [MAIN, VARIANT],
                "description": case["description"],
                "lines": lines,
                "values": case["values"],
            }
        )

    expected = {
        "fixture": "two-analyte",
        "generator": "tests/fixtures/make_fixtures.py",
        "seed": SEED,
        "rules": RULES,
        "standard_setup": STANDARD_SETUP,
        "files": files,
        "cases": cases,
        "findings": {MAIN: _findings(rows, False), VARIANT: _findings(variant, True)},
        "scenarios": {MAIN: _scenarios(rows)},
        "not_plantable": {
            "session_exclusion_no_reason": "Comes from exclusions made inside an opnca-r "
            "session, which records no reason. No input file can plant it."
        },
    }
    text = json.dumps(expected, indent=2, ensure_ascii=False) + "\n"
    return {MAIN: _csv_bytes(rows), VARIANT: _csv_bytes(variant), EXPECTED: text.encode("utf-8")}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=Path(__file__).resolve().parent)
    args = parser.parse_args(argv)
    if not args.out.is_dir():
        parser.error(f"output directory does not exist: {args.out}")
    for name, data in generate().items():
        (args.out / name).write_bytes(data)
        print(f"wrote {args.out / name} ({len(data)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
