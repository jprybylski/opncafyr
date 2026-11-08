"""The unit labels a run passes to opnca-r (#57): from the frozen rows, never a default."""

from __future__ import annotations

import pytest

from nca_helpers import fixture_table, resolving_mapping
from opncafyr.workflows.nca import checks as C
from opncafyr.workflows.nca.units import UnitLabelError, analysis_labels


def _records(name: str, analyte: str = "PARENT") -> list[dict[str, str]]:
    header, rows = fixture_table(name)
    records, _ = C.select(header, rows, resolving_mapping(), C.Selection(analyte, "PLASMA"))
    return records


def test_labels_from_unit_columns_and_a_declared_value():
    units = resolving_mapping()["units"]
    assert "column" in units["CONC"] and "value" in units["ACTUAL_TIME"]
    labels = analysis_labels(units, _records("two_analyte.csv"))
    assert labels == {"concentration": "ng/mL", "time": "h", "dose": "mg"}
    assert list(labels) == ["concentration", "time", "dose"]


def test_a_non_default_triple_in_the_launchers_spelling():
    units = {"ACTUAL_TIME": {"value": "mins"}, "CONC": {"value": "µg/l"}, "DOSE": {"value": "mcg"}}
    assert analysis_labels(units, []) == {"concentration": "ug/L", "time": "min", "dose": "ug"}


def test_spelling_variants_in_a_column_are_one_unit_and_blanks_are_skipped():
    units = {"ACTUAL_TIME": {"value": "h"}, "CONC": {"column": "U"}, "DOSE": {"value": "mg"}}
    rows = [{"U": "ng/ml"}, {"U": "ng/mL "}, {"U": ""}, {"U": "NG/ML"}]
    assert analysis_labels(units, rows)["concentration"] == "ng/mL"


def test_the_mixed_unit_fixture_is_not_labelled():
    with pytest.raises(UnitLabelError) as err:
        analysis_labels(resolving_mapping()["units"], _records("two_analyte_mixed_units.csv"))
    assert err.value.role == "CONC" and "more than one" in str(err.value)


@pytest.mark.parametrize(
    ("units", "rows", "role"),
    [
        ({"CONC": {"value": "ng/mL"}, "DOSE": {"value": "mg"}}, [], "ACTUAL_TIME"),
        (
            {
                "ACTUAL_TIME": {"value": "fortnight"},
                "CONC": {"value": "ng/mL"},
                "DOSE": {"value": "mg"},
            },
            [],
            "ACTUAL_TIME",
        ),
        (
            {"ACTUAL_TIME": {"value": "h"}, "CONC": {"column": "U"}, "DOSE": {"value": "mg"}},
            [{"U": ""}, {"U": " "}],
            "CONC",
        ),
        (
            {"ACTUAL_TIME": {"value": "h"}, "CONC": {"column": "U"}, "DOSE": {"value": "mg"}},
            [{"U": "nmol/L"}],
            "CONC",
        ),
        (
            {"ACTUAL_TIME": {"column": "TU"}, "CONC": {"value": "ng/mL"}, "DOSE": {"value": "mg"}},
            [{"TU": "h"}, {"TU": "min"}],
            "ACTUAL_TIME",
        ),
    ],
)
def test_missing_unknown_or_mixed_units_never_fall_back(units, rows, role):
    with pytest.raises(UnitLabelError) as err:
        analysis_labels(units, rows)
    assert err.value.role == role
