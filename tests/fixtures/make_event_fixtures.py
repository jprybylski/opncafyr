"""Generate the event-record and single-analyte test fixtures (#58).

Usage (from the repository root):

    uv run python tests/fixtures/make_event_fixtures.py [--out DIR]

DIR defaults to this script's folder. The script writes:

- ``nonmem_events.csv``: NONMEM-style event records for six synthetic subjects: one oral
  dose record each (EVID 1, CMT 1), PK observations of a parent (CMT 2) and a metabolite
  (CMT 3), a non-PK endpoint (CMT 4), predose samples with MDV 1, BLQ samples with CENS 1
  and per-record units in EVENTU (mg on doses, ng/mL or IU/L on observations);
- ``nonmem_events_planted.csv``: the same layout with one planted case per event-stage
  check (ADR 0005 Q9), one subject each;
- ``generic_single.csv``: a loosely structured single-analyte table with units only in
  its headers and no analyte or route column;
- ``events.expected.json``: the standard setup (recipe steps, events block, mapping), the
  partition of every line of ``nonmem_events.csv`` with its dose and time after dose, the
  planted cases with their lines and the check each raises when nothing is chosen, and the
  generic file's planted case.

The output is deterministic (fixed seed, fixed number formats, ``\\n`` line endings).
``tests/test_fixtures.py`` fails if the committed files differ from a fresh run. Never
hand-edit the outputs; change this script and regenerate. Every subject is synthetic.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
import random
from pathlib import Path

SEED = 20261108
MAIN = "nonmem_events.csv"
PLANTED = "nonmem_events_planted.csv"
GENERIC = "generic_single.csv"
EXPECTED = "events.expected.json"
COLUMNS = [
    "ID",
    "TIME",
    "NOMTIME",
    "TIMEUNIT",
    "AMT",
    "DV",
    "MDV",
    "EVID",
    "CMT",
    "NAME",
    "EVENTU",
    "CENS",
]
NAMES = {"1": "Dosing", "2": "Parent", "3": "Metabolite", "4": "Biomarker"}
UNITS = {"1": "mg", "2": "ng/mL", "3": "ng/mL", "4": "IU/L"}
PK_TIMES = [0.5, 1, 2, 4, 8, 12, 24]
PD_TIMES = [0, 4, 24]
DOSE_MG = 100
METAB_LLOQ = "0.5"

STANDARD_STEPS = [
    {
        "op": "derive",
        "note": "The file has no route column; every dose is oral.",
        "args": {"target": "route", "fn": "constant", "value": "extravascular"},
    }
]
STANDARD_EVENTS = {
    "columns": {
        "subject": "ID",
        "time": "TIME",
        "record_kind": "EVID",
        "amount": "AMT",
        "endpoint": "CMT",
        "route": "route",
        "dv": "DV",
        "mdv": "MDV",
        "dose_unit": "EVENTU",
        "time_unit": "TIMEUNIT",
    },
    "record_kinds": {"0": "observation", "1": "dose"},
    "endpoints": {"2": "pk", "3": "pk", "4": "other"},
    "mdv": {"0": False, "1": True},
    "choices": {"predose": "keep"},
}
STANDARD_MAPPING = {
    "roles": {
        "SUBJECT_ID": "ID",
        "ANALYTE": "CMT",
        "ANALYTE_LABEL": "NAME",
        "ACTUAL_TIME": "opncafyr_time_after_dose",
        "CONC": "DV",
        "BLQ": "CENS",
        "MISSING": "MDV",
        "DOSE": "opncafyr_dose",
        "ROUTE": "opncafyr_route",
    },
    "units": {
        "ACTUAL_TIME": {"column": "TIMEUNIT"},
        "CONC": {"column": "EVENTU"},
        "DOSE": {"column": "opncafyr_dose_unit"},
    },
    "flags": {
        "BLQ": {"table": {"0": False, "1": True}},
        "MISSING": {"table": {"0": False, "1": True}},
    },
}


def _num(value: float) -> str:
    return f"{value:.4g}" if value >= 1 else f"{value:.3g}"


def _time(value: float) -> str:
    return f"{value:.3f}".rstrip("0").rstrip(".") if value != int(value) else str(int(value))


def _parent(t: float, ka: float, ke: float, v: float) -> float:
    return 1000 * DOSE_MG * ka / (v * (ka - ke)) * (math.exp(-ke * t) - math.exp(-ka * t))


def _record(sid: str, time: str, nom: str, evid: str, cmt: str, **kw: str) -> dict[str, str]:
    row = {
        "ID": sid,
        "TIME": time,
        "NOMTIME": nom,
        "TIMEUNIT": "Hours",
        "AMT": "0",
        "DV": ".",
        "MDV": "1",
        "EVID": evid,
        "CMT": cmt,
        "NAME": NAMES.get(cmt, "Unknown"),
        "EVENTU": UNITS.get(cmt, "ng/mL"),
        "CENS": "0",
    }
    row.update(kw)
    return row


class Main:
    """Six subjects, one dose each. Every line's expected partition is recorded."""

    def __init__(self, rng: random.Random) -> None:
        self.rng = rng
        self.rows: list[dict[str, str]] = []
        self.expect: list[dict[str, object]] = []

    def add(self, row: dict[str, str], partition: str, **extra: object) -> int:
        self.rows.append(row)
        self.expect.append({"partition": partition, **extra})
        return len(self.rows) + 1  # physical line (header = 1)

    def build(self) -> None:
        for i in range(6):
            sid = str(101 + i)
            ka = 1.0 + self.rng.uniform(-0.2, 0.3)
            ke = 0.12 + self.rng.uniform(-0.02, 0.03)
            v = 40 + self.rng.uniform(-5, 10)
            pre = -round(0.2 + self.rng.uniform(0, 0.15), 3)
            dose_line = len(self.rows) + 4  # after the two predose rows (header = line 1)
            for cmt in ("2", "3"):
                self.add(
                    _record(sid, _time(pre), "-0.25", "0", cmt),
                    "selected",
                    dose_lines=str(dose_line),
                    time_after_dose=_time(pre),
                    endpoint=cmt,
                )
            line = self.add(_record(sid, "0", "0", "1", "1", AMT=str(DOSE_MG), EVENTU="mg"), "dose")
            assert line == dose_line
            pd_line = self.add(
                _record(sid, "0", "0", "0", "4", DV=_num(10 + self.rng.uniform(0, 2)), MDV="0"),
                "other_endpoint",
            )
            del pd_line
            for nom in PK_TIMES:
                t = round(nom + self.rng.uniform(-0.05, 0.08) * max(nom, 1) / 4, 3)
                parent = _parent(t, ka, ke, v)
                metab = 0.08 * parent * (1 - math.exp(-0.3 * t))
                for cmt, value in (("2", parent), ("3", metab)):
                    blq = cmt == "3" and nom == 24 and i in (2, 5)
                    dv = METAB_LLOQ if blq else _num(value)
                    row = _record(
                        sid,
                        _time(t),
                        _time(nom),
                        "0",
                        cmt,
                        DV=dv,
                        MDV="0",
                        CENS="1" if blq else "0",
                    )
                    self.add(
                        row,
                        "selected",
                        dose_lines=str(dose_line),
                        time_after_dose=_time(t),
                        endpoint=cmt,
                    )
                if nom in PD_TIMES[1:]:
                    self.add(
                        _record(
                            sid,
                            _time(t),
                            _time(nom),
                            "0",
                            "4",
                            DV=_num(10 + self.rng.uniform(-3, 3)),
                            MDV="0",
                        ),
                        "other_endpoint",
                    )


def _planted() -> tuple[list[dict[str, str]], list[dict[str, object]]]:
    """One subject per case. ``check`` is what the case raises with no choice made."""
    rows: list[dict[str, str]] = []
    cases: list[dict[str, object]] = []

    def add(row: dict[str, str]) -> int:
        rows.append(row)
        return len(rows) + 1

    def obs(sid: str, t: str, cmt: str = "2", dv: str = "5.2", **kw: str) -> int:
        return add(_record(sid, t, t, "0", cmt, **{"DV": dv, "MDV": "0", **kw}))

    def dose(sid: str, t: str, amt: str = "100", **kw: str) -> int:
        return add(_record(sid, t, t, "1", "1", **{"AMT": amt, "EVENTU": "mg", **kw}))

    def case(cid: str, check: str, lines: list[int], description: str, **extra: object) -> None:
        cases.append(
            {"id": cid, "check": check, "lines": lines, "description": description, **extra}
        )

    # P01: two doses 24 h apart; samples at 1, 4, 24 (the second dose time), 25 and 28 h.
    d1 = dose("P01", "0")
    o1 = [obs("P01", "1"), obs("P01", "4")]
    d2 = dose("P01", "24")
    at = obs("P01", "24")
    o2 = [obs("P01", "25"), obs("P01", "28")]
    case(
        "multiple_doses",
        "event_multiple_doses",
        [*o1, at, *o2],
        "Two dose records (0 h and 24 h) and no interval rule.",
        doses=[d1, d2],
        first=o1,
        at_dose_time=at,
        last=o2,
    )
    # P02: a split dose, two records at 0 h (0.1 mg and 0.2 mg).
    s = [dose("P02", "0", "0.1"), dose("P02", "0", "0.2")]
    o = [obs("P02", "1"), obs("P02", "2")]
    case(
        "same_time_doses",
        "event_same_time_doses",
        s,
        "Two dose records at the same time and no same-time rule; summed, the dose is 0.3.",
        observations=o,
        observation_check="event_dose_unresolved",
        summed="0.3",
    )
    # P03: no dose record at all.
    o = [obs("P03", "1"), obs("P03", "2")]
    case("unmatched", "event_unmatched", o, "A subject with observations and no dose record.")
    # P04: a dose record with AMT 0.
    d = dose("P04", "0", "0")
    o = [obs("P04", "1")]
    case(
        "dose_amount_invalid",
        "event_dose_amount_invalid",
        [d],
        "A dose record whose amount is 0.",
        observations=o,
        observation_check="event_dose_unresolved",
    )
    # P05: a record kind and an endpoint code that are not in the tables.
    dose("P05", "0")
    k = add(_record("P05", "12", "12", "4", "1", AMT="100", EVENTU="mg"))
    e = obs("P05", "2", cmt="9")
    obs("P05", "1")
    case("kind_unknown", "event_kind_unknown", [k], "EVID 4 is not in the record-kind table.")
    case("endpoint_unknown", "event_endpoint_unknown", [e], "CMT 9 is not in the endpoint table.")
    # P06: MDV says the dose record is an observation; an observation with MDV 1 has a DV.
    d = dose("P06", "0", MDV="0")
    o = [obs("P06", "1")]
    c = obs("P06", "2", MDV="1")
    case(
        "flag_conflict",
        "event_flag_conflict",
        [d, c],
        "A dose record with MDV 0, and an observation with MDV 1 and a numeric DV.",
        observations=o,
        observation_check="event_dose_unresolved",
    )
    # P07: an observation whose time unit is not its dose's; a predose sample.
    dose("P07", "0")
    p = obs("P07", "-0.5")
    obs("P07", "1")
    u = obs("P07", "120", TIMEUNIT="Minutes")
    case(
        "time_units_differ",
        "event_time_units_differ",
        [u],
        "An observation in minutes; its dose is in hours.",
    )
    case("predose", "event_predose", [p], "A sample before the first dose and no predose rule.")
    # P08: an observation time that is not a number.
    dose("P08", "0")
    t = obs("P08", "NA")
    obs("P08", "1")
    case("time_not_numeric", "event_time_not_numeric", [t], "An observation with no time.")
    # P09: EVENTU changes between two observations of one endpoint. Not the event stage's
    # to judge: the units checks block it on the prepared rows.
    dose("P09", "0")
    m = [obs("P09", "1"), obs("P09", "2", EVENTU="ug/mL")]
    case(
        "conc_units_mixed",
        "units_mixed",
        [m[1]],
        "Two observations of one endpoint with different EVENTU units.",
        found_by="checks",
    )
    # P10: a split dose whose records disagree on the unit: not summable even by choice.
    s = [dose("P10", "0", "50"), dose("P10", "0", "50", EVENTU="ug")]
    obs("P10", "1")
    case(
        "same_time_doses_differ",
        "event_same_time_doses",
        s,
        "Two dose records at one time in mg and ug; with 'sum' they still can't be summed.",
        with_sum="event_same_time_doses_differ",
    )
    # P11: an MDV value outside the table.
    dose("P11", "0")
    v = obs("P11", "1", MDV="9")
    case("mdv_unmapped", "event_mdv_unmapped", [v], "MDV 9 is not in the MDV table.")
    return rows, cases


def _generic(rng: random.Random) -> tuple[list[list[str]], list[str], dict[str, object]]:
    header = ["Subject ID", "Time (hr)", "Conc.(ng/mL)", "Dose mg", "Weight kg"]
    rows: list[list[str]] = []
    missing_line = 0
    for i in range(6):
        sid = f"S-{i + 1:02d}"
        ka, ke, v = 1.1 + rng.uniform(-0.2, 0.2), 0.15 + rng.uniform(-0.03, 0.03), 45.0
        weight = f"{60 + rng.uniform(0, 30):.1f}"
        for t in (0, 0.5, 1, 2, 4, 8, 12, 24):
            conc = "0" if t == 0 else _num(_parent(t, ka, ke, v) / 2)
            if i == 3 and t == 8:
                conc = "."
                missing_line = len(rows) + 2
            rows.append([sid, _time(t), conc, "50", weight])
    case = {
        "id": "conc_not_analysed",
        "check": "conc_unclassified_blank",
        "lines": [missing_line],
        "description": "A sample written '.', with no flag saying why it is missing.",
    }
    return rows, header, case


GENERIC_STEPS = [
    {"op": "clean_names", "args": {}},
    {
        "op": "derive",
        "note": "The file holds one analyte and names none.",
        "args": {"target": "analyte", "fn": "constant", "value": "DRUGX"},
    },
    {
        "op": "derive",
        "note": "Every dose is oral.",
        "args": {"target": "route", "fn": "constant", "value": "extravascular"},
    },
]
GENERIC_MAPPING = {
    "roles": {
        "SUBJECT_ID": "subject_id",
        "ANALYTE": "analyte",
        "ACTUAL_TIME": "time_hr",
        "CONC": "conc_ng_ml",
        "DOSE": "dose_mg",
        "ROUTE": "route",
    },
    "units": {
        "ACTUAL_TIME": {"value": "h"},
        "CONC": {"value": "ng/mL"},
        "DOSE": {"value": "mg"},
    },
}


def _csv(header: list[str], rows: list[list[str]]) -> bytes:
    buf = io.StringIO(newline="")
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(header)
    writer.writerows(rows)
    return buf.getvalue().encode("utf-8")


def _records(rows: list[dict[str, str]]) -> list[list[str]]:
    return [[r[c] for c in COLUMNS] for r in rows]


def generate() -> dict[str, bytes]:
    rng = random.Random(SEED)  # noqa: S311 - reproducible synthetic data
    main = Main(rng)
    main.build()
    planted, cases = _planted()
    generic, generic_header, generic_case = _generic(rng)
    lines = {str(i + 2): e for i, e in enumerate(main.expect)}
    counts: dict[str, int] = {}
    for e in main.expect:
        counts[str(e["partition"])] = counts.get(str(e["partition"]), 0) + 1
    expected = {
        "fixture": "event-records",
        "generator": "tests/fixtures/make_event_fixtures.py",
        "seed": SEED,
        "standard_setup": {
            "steps": STANDARD_STEPS,
            "events": STANDARD_EVENTS,
            "mapping": STANDARD_MAPPING,
        },
        "files": {
            MAIN: {
                "rows": len(main.rows),
                "columns": COLUMNS,
                "partitions": counts,
                "lines": lines,
            },
            PLANTED: {"rows": len(planted), "columns": COLUMNS, "cases": cases},
            GENERIC: {
                "rows": len(generic),
                "columns": generic_header,
                "steps": GENERIC_STEPS,
                "mapping": GENERIC_MAPPING,
                "cases": [generic_case],
            },
        },
    }
    text = json.dumps(expected, indent=2, ensure_ascii=False) + "\n"
    return {
        MAIN: _csv(COLUMNS, _records(main.rows)),
        PLANTED: _csv(COLUMNS, _records(planted)),
        GENERIC: _csv(generic_header, generic),
        EXPECTED: text.encode("utf-8"),
    }


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
