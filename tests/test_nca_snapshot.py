"""Snapshot rules that need no R (#25)."""

from __future__ import annotations

import pytest

from nca_helpers import fixture_table, resolving_mapping
from opncafyr.workflows.nca import checks as C
from opncafyr.workflows.nca import preset as P
from opncafyr.workflows.nca import snapshot as S


@pytest.mark.parametrize(
    ("name", "ok"),
    [
        ("USUBJID", True),
        ("opncafyr_line", True),
        ("x.1", True),
        (".hidden", True),
        ("Conc (ng/mL)", False),
        ("1st", False),
        (".1x", False),
        ("if", False),
        ("TRUE", False),
        ("µg", False),
        ("a b", False),
    ],
)
def test_r_safe_names(name, ok):
    assert S.r_safe_name(name) is ok


def test_numeric_ids_that_would_merge():
    assert S._numeric_ids(["01", "1", "2"]) is True
    assert S._numeric_ids(["1", "2", "3"]) is False
    assert S._numeric_ids(["TA-001", "TA-01"]) is False


def _snapshot(analyte):
    header, rows = fixture_table()
    records, _ = C.select(header, rows, resolving_mapping(), C.Selection(analyte, "PLASMA"))
    return S.build_rows(header, records, resolving_mapping(), C.Selection(analyte, "PLASMA"))


def test_snapshot_columns_never_write_zero_for_blq():
    header, rows = _snapshot("METAB")
    i = {h: n for n, h in enumerate(header)}
    for r in rows:
        assert r[i["NCA_BLQ"]] in ("TRUE", "FALSE")
        if r[i["CONC"]].startswith("<"):
            assert r[i["NCA_BLQ"]] == "TRUE" and r[i["NCA_CONC"]] == ""
        if r[i["CONC"]] == "":
            assert r[i["NCA_CONC"]] == ""
        if r[i["NCA_CONC"]]:
            assert r[i["NCA_CONC"]] == r[i["CONC"]]  # the source text, unchanged


def test_excluded_rows_stay_with_their_reason():
    header, rows = _snapshot("PARENT")
    i = {h: n for n, h in enumerate(header)}
    excluded = [r for r in rows if r[i["NCA_EXCLUDED"]] == "TRUE"]
    assert excluded and all(
        r[i["NCA_EXCLUSION_REASON"]] or r[i["opncafyr_line"]] == "24" for r in excluded
    )


def test_existing_nca_columns_clash():
    header, rows = fixture_table()
    header = [*header, "NCA_BLQ"]
    with pytest.raises(S.SnapshotError) as err:
        S.build_rows(header, [], resolving_mapping(), C.Selection("PARENT", "PLASMA"))
    assert err.value.code == "column_clash"


def test_blq_choice_has_no_default():
    with pytest.raises(S.SnapshotError) as err:
        S._check_blq({"blq_predose": "zero"}, None)
    assert err.value.code == "needs_choice"
    assert set(err.value.extra["missing"]) == {"blq_postdose", "blq_terminal", "blq_method"}
    with pytest.raises(S.SnapshotError):
        S._check_blq(
            {"blq_predose": "zero", "blq_postdose": "zero", "blq_terminal": "drop"}, "conventional"
        )


def test_preset_names_every_role():
    header, _ = _snapshot("PARENT")
    blq = S._check_blq(
        {"blq_predose": "zero", "blq_postdose": "exclude", "blq_terminal": "exclude"},
        "conventional",
    )
    preset = P.build_preset(resolving_mapping(), S.SNAPSHOT_COLUMNS, blq, header)
    assert set(P.REQUIRED + P.OPTIONAL + P.SETTINGS) <= set(preset)
    assert preset["concentration"] == "NCA_CONC" and preset["blq"] == "NCA_BLQ"
    assert preset["infusion_duration"] == ""
    with pytest.raises(P.PresetError):
        P.check_preset({**preset, "guess": "x"}, header)
    with pytest.raises(P.PresetError):
        P.check_preset({**preset, "lloq": "NOPE"}, header)
    missing_opt = dict(preset)
    del missing_opt["nominal_time"]
    with pytest.raises(P.PresetError):
        P.check_preset(missing_opt, header)
