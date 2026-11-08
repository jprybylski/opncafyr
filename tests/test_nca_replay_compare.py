"""Replay comparison: exact identifiers and counts, relative tolerance for numbers (#29)."""

from __future__ import annotations

import pytest

from opncafyr.workflows.nca.replay_compare import (
    compare_folders,
    compare_tables,
    describe,
    not_reproduced,
)

HEADER = "profile_id,metric,value,unit,status,n_contributing\n"
ROW = "TA-001,cmax,123.456789,ng/mL,ok,8\n"


def _write(folder, name, text):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_text(text, "utf-8")


def _pair(tmp_path, saved, replayed, name="opnca-parameters-long.csv"):
    _write(tmp_path / "results", name, saved)
    _write(tmp_path / "replay", name, replayed)
    return compare_folders(tmp_path / "results", tmp_path / "replay", rtol=1e-10)


def test_identical_tables_pass(tmp_path):
    report = _pair(tmp_path, HEADER + ROW, HEADER + ROW)
    assert report["status"] == "passed" and report["tables"][0]["ok"]


def test_numbers_within_tolerance_pass(tmp_path):
    report = _pair(tmp_path, HEADER + ROW, HEADER + ROW.replace("123.456789", "123.4567890000001"))
    assert report["status"] == "passed"
    assert 0 < report["tables"][0]["max_rel_diff"] < 1e-10


def test_numbers_beyond_tolerance_fail_readably(tmp_path):
    report = _pair(tmp_path, HEADER + ROW, HEADER + ROW.replace("123.456789", "123.456"))
    assert report["status"] == "failed"
    m = report["tables"][0]["mismatches"][0]
    assert (m["row"], m["column"], m["saved"], m["replayed"]) == (
        1,
        "value",
        "123.456789",
        "123.456",
    )
    text = describe(report)
    assert "opnca-parameters-long.csv row 1, value: saved '123.456789', replayed '123.456'" in text


@pytest.mark.parametrize(
    ("old", "new", "column"),
    [
        ("TA-001", "TA-002", "profile_id"),
        (",ok,", ",flagged,", "status"),
        (",8\n", ",8.0000000000001\n", "n_contributing"),
        ("ng/mL", "ug/L", "unit"),
    ],
)
def test_identifiers_counts_and_status_must_be_equal(tmp_path, old, new, column):
    report = _pair(tmp_path, HEADER + ROW, HEADER + ROW.replace(old, new))
    assert report["status"] == "failed"
    assert report["tables"][0]["mismatches"][0]["column"] == column


def test_blanks_must_match(tmp_path):
    report = _pair(tmp_path, HEADER + ROW.replace("123.456789", ""), HEADER + ROW)
    assert report["status"] == "failed"
    zero = HEADER + ROW.replace("123.456789", "0")
    assert _pair(tmp_path / "z", zero, zero)["status"] == "passed"


def test_shape_differences_fail(tmp_path):
    assert _pair(tmp_path / "a", HEADER + ROW, HEADER + ROW + ROW)["status"] == "failed"
    other = HEADER.replace("value", "estimate")
    report = _pair(tmp_path / "b", HEADER + ROW, other + ROW)
    assert report["tables"][0]["problem"] == "The columns differ."


def test_a_missing_replay_table_fails(tmp_path):
    _write(tmp_path / "results", "opnca-summary.csv", HEADER + ROW)
    (tmp_path / "replay").mkdir()
    report = compare_folders(tmp_path / "results", tmp_path / "replay")
    assert report["status"] == "failed" and "did not write" in describe(report)


def test_no_tables_is_not_a_pass(tmp_path):
    (tmp_path / "results").mkdir()
    (tmp_path / "replay").mkdir()
    assert compare_folders(tmp_path / "results", tmp_path / "replay")["status"] == "failed"


def test_one_table(tmp_path):
    _write(tmp_path, "a.csv", HEADER + ROW)
    _write(tmp_path / "r", "a.csv", HEADER + ROW)
    assert compare_tables(tmp_path / "a.csv", tmp_path / "r" / "a.csv")["ok"] is True


def test_tables_analysis_r_does_not_replay_are_listed_not_compared(tmp_path):
    _write(tmp_path / "results", "opnca-parameters-long.csv", HEADER + ROW)
    _write(tmp_path / "replay", "opnca-parameters-long.csv", HEADER + ROW)
    _write(tmp_path / "results", "bioequivalence-summary.csv", "metric\ncmax\n")
    listing = {
        "schema_version": 2,
        "artifacts": [
            {"path": "results/opnca-parameters-long.csv", "replay": {"reproduced": True}},
            {
                "path": "results/bioequivalence-summary.csv",
                "replay": {"reproduced": False, "reason": "not replayed"},
            },
            {"path": "results/tables/x.rds", "replay": {"reproduced": False, "reason": "rds"}},
            {"path": "analysis.R"},
        ],
    }
    skip = not_reproduced(listing)
    assert skip == {"bioequivalence-summary.csv": "not replayed"}
    report = compare_folders(tmp_path / "results", tmp_path / "replay", skip=skip)
    assert report["status"] == "passed"
    assert [t["file"] for t in report["tables"]] == ["opnca-parameters-long.csv"]
    assert report["not_reproduced"] == [
        {"file": "bioequivalence-summary.csv", "reason": "not replayed"}
    ]
    # Without the listing the table is compared, and its missing replay fails.
    assert compare_folders(tmp_path / "results", tmp_path / "replay")["status"] == "failed"


def test_a_version_1_listing_skips_nothing():
    assert not_reproduced({"artifacts": [{"path": "results/a.csv", "sha256": "0" * 64}]}) == {}
