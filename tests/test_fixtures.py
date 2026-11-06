"""The two-analyte fixture (#20) is generated, reproducible and self-consistent."""

import csv
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

FIXTURES = Path(__file__).resolve().parent / "fixtures"
GENERATOR = FIXTURES / "make_fixtures.py"
MAIN = "two_analyte.csv"
VARIANT = "two_analyte_mixed_units.csv"
EXPECTED = "two_analyte.expected.json"

# ADR 0005 Q7: every blocking and warning check must be planted, triggered by a
# scenario, or explained as not plantable.
BLOCKING_AND_WARNING = {
    "role_unmapped",
    "analyte_no_rows",
    "specimen_mixed",
    "time_not_numeric",
    "conc_not_numeric",
    "conc_unclassified_blank",
    "conc_negative",
    "route_missing",
    "route_unrecognised",
    "route_varies",
    "dose_varies",
    "units_mixed",
    "units_undeclared",
    "duplicate_key",
    "blq_and_missing",
    "flag_value_unmapped",
    "exclusion_no_reason",
    "time_nominal_as_actual",
    "time_clock_format",
    "time_derivation_failed",
    "profile_no_dose",
    "conc_zero_not_blq",
    "blq_above_lloq",
    "lloq_varies",
    "time_negative",
    "id_usubjid_not_one_to_one",
    "session_exclusion_no_reason",
}


def _generator():
    spec = importlib.util.spec_from_file_location("make_fixtures", GENERATOR)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses look their module up here
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def expected() -> dict:
    return json.loads((FIXTURES / EXPECTED).read_text("utf-8"))


def _rows(name: str) -> list[dict[str, str]]:
    with (FIXTURES / name).open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _line(rows: list[dict[str, str]], line: int) -> dict[str, str]:
    assert 2 <= line <= len(rows) + 1, f"line {line} is outside the file"
    return rows[line - 2]


def test_committed_fixtures_match_the_generator(tmp_path):
    subprocess.run([sys.executable, str(GENERATOR), "--out", str(tmp_path)], check=True)
    for name in (MAIN, VARIANT, EXPECTED):
        fresh = (tmp_path / name).read_bytes()
        assert fresh == (FIXTURES / name).read_bytes(), (
            f"{name} differs from a fresh run of tests/fixtures/make_fixtures.py"
        )


def test_generator_is_deterministic():
    gen = _generator()
    assert gen.generate() == gen.generate()


def test_files_use_lf_and_no_field_has_a_newline():
    for name in (MAIN, VARIANT):
        data = (FIXTURES / name).read_bytes()
        assert b"\r" not in data
        assert len(data.splitlines()) == len(_rows(name)) + 1


def test_counts_match_the_csv(expected):
    for name in (MAIN, VARIANT):
        rows = _rows(name)
        info = expected["files"][name]
        assert info["rows"] == len(rows)
        assert info["columns"] == list(rows[0])
        for code, counts in info["analytes"]["values"].items():
            mine = [r for r in rows if r["PCTESTCD"] == code]
            assert counts["rows"] == len(mine)
            assert counts["subjects"] == len({r["USUBJID"] for r in mine})
            for spec, n in counts["by_specimen"].items():
                assert n == sum(r["PCSPEC"] == spec for r in mine)
        assert sum(c["rows"] for c in info["analytes"]["values"].values()) == len(rows)


def test_the_two_analytes_share_subject_and_time_keys(expected):
    rows = [r for r in _rows(MAIN) if r["PCSPEC"] == "PLASMA"]
    keys = {
        a: {(r["USUBJID"], r["AFRLT"]) for r in rows if r["PCTESTCD"] == a}
        for a in ("PARENT", "METAB")
    }
    shared = keys["PARENT"] & keys["METAB"]
    assert len(shared) == expected["files"][MAIN]["analytes"]["shared_plasma_keys"]
    # Only the planted rows are outside the overlap: the invalid extra PARENT sample, and
    # TA-005 8 h, whose METAB time is written '~8'.
    parent_8h = next(
        r["AFRLT"]
        for r in rows
        if r["USUBJID"] == "TA-005" and r["PCTESTCD"] == "PARENT" and r["NFRLT"] == "8"
    )
    assert keys["PARENT"] - shared == {("TA-007", "6.0833"), ("TA-005", parent_8h)}
    assert keys["METAB"] - shared == {("TA-005", "~8")}


def test_every_case_line_carries_its_planted_values(expected):
    for case in expected["cases"]:
        assert case["lines"], case["id"]
        for name in case["files"]:
            rows = _rows(name)
            for line in case["lines"]:
                row = _line(rows, line)
                for column, value in case["values"].items():
                    assert row[column] == value, (case["id"], name, line, column)


def test_findings_point_at_rows_of_their_analyte_and_subject(expected):
    for name, findings in expected["findings"].items():
        rows = _rows(name)
        for f in findings:
            assert f["lines"] == sorted(f["lines"]), f
            for line in f["lines"]:
                row = _line(rows, line)
                assert row["PCTESTCD"] == f["analyte"], (name, f["check"], line)
                assert row["PCSPEC"] == "PLASMA", (name, f["check"], line)
                if f["subject"] is not None:
                    assert row["USUBJID"] == f["subject"], (name, f["check"], line)


def test_variant_findings_are_the_main_findings_plus_mixed_units(expected):
    main = expected["findings"][MAIN]
    variant = expected["findings"][VARIANT]
    assert variant[: len(main)] == main
    assert [f["check"] for f in variant[len(main) :]] == ["units_mixed"]


def test_every_blocking_and_warning_check_is_covered(expected):
    found = {f["check"] for fs in expected["findings"].values() for f in fs}
    scenario = {
        i["check"] for ss in expected["scenarios"].values() for s in ss for i in s["includes"]
    }
    unplantable = set(expected["not_plantable"])
    covered = found | scenario | unplantable
    assert covered >= BLOCKING_AND_WARNING


def test_levels_follow_adr_0005(expected):
    levels = _generator().LEVELS
    for fs in expected["findings"].values():
        for f in fs:
            assert f["level"] == levels[f["check"]]
    for ss in expected["scenarios"].values():
        for s in ss:
            for i in s["includes"]:
                assert i["level"] == levels[i["check"]]


def test_every_case_is_documented(expected):
    readme = (FIXTURES / "README.md").read_text("utf-8")
    for case in expected["cases"]:
        assert f"`{case['id']}`" in readme, case["id"]
    for ss in expected["scenarios"].values():
        for s in ss:
            assert f"`{s['id']}`" in readme, s["id"]
