"""Column-role suggestions, units, routes and the mapping block (#23)."""

from __future__ import annotations

import copy
import csv

import pytest

from nca_helpers import EXPECTED, FIXTURES, make_study, nca_client, new_recipe, standard_mapping
from opncafyr.workflows.nca import mapping as M
from opncafyr.workflows.nca.roles import THRESHOLD, Column, columns_from_rows, suggest
from opncafyr.workflows.nca.routes import canonical_route, route_values
from opncafyr.workflows.nca.units import header_hint, normalise


def _suggest(name):
    with (FIXTURES / name).open(newline="", encoding="utf-8") as fh:
        rows = list(csv.reader(fh))
    return suggest(columns_from_rows(rows[0], rows[1:]))


def _picked(out):
    return {s["role"]: s["column"] for s in out["suggestions"]}


def test_two_analyte_suggestions_match_the_standard_setup():
    out = _suggest("two_analyte.csv")
    picked = _picked(out)
    standard = EXPECTED["standard_setup"]["mapping"]
    assert {r: picked.get(r) for r in standard} == standard
    # The one extra suggestion is the secondary ID the fixture's scenario maps.
    scenario = next(
        s for s in EXPECTED["scenarios"]["two_analyte.csv"] if s["id"] == "secondary_id_mapped"
    )
    assert set(picked) - set(standard) == set(scenario["change"]["mapping"])
    assert picked["SECONDARY_ID"] == scenario["change"]["mapping"]["SECONDARY_ID"]
    assert out["needs_choice"] == []
    for s in out["suggestions"]:
        assert s["confidence"] >= THRESHOLD and s["reasons"]


def test_adpc_suggestions_and_questions():
    out = _suggest("adpc.csv")
    assert _picked(out) == {
        "SUBJECT_ID": "USUBJID",
        "ANALYTE": "PARAMCD",
        "ANALYTE_LABEL": "PARAM",
        "ACTUAL_TIME": "AFRLT",
        "NOMINAL_TIME": "NFRLT",
        "CONC": "AVAL",
        "CONC_UNIT": "AVALU",
        "BLQ": "BLQFL",
    }
    # No dose or route in an ADPC: asked, never defaulted. ANL01FL is never an exclusion.
    asked = {q["role"] for q in out["needs_choice"]}
    assert asked == {"DOSE", "ROUTE"}
    for q in out["needs_choice"]:
        assert q["code"] == "needs_choice" and q["choices"]


def _cols(**columns):
    return [Column(name, values) for name, values in columns.items()]


def test_two_actual_time_columns_are_a_question():
    ids = ["A", "A", "B", "B"]
    out = suggest(_cols(USUBJID=ids, AFRLT=["0", "1", "0", "1"], ARRLT=["0", "1", "0", "1"]))
    q = next(q for q in out["needs_choice"] if q["role"] == "ACTUAL_TIME")
    assert {c["value"] for c in q["choices"]} == {"AFRLT", "ARRLT"}
    assert "ACTUAL_TIME" not in _picked(out)


def test_a_weak_match_is_a_question():
    out = suggest(_cols(USUBJID=["A", "A"], PK_CONC_VALUE=["1", "2"]))
    q = next(q for q in out["needs_choice"] if q["role"] == "CONC")
    assert q["choices"][0]["value"] == "PK_CONC_VALUE"
    assert q["choices"][0]["confidence"] < THRESHOLD


def test_nominal_time_is_never_suggested_as_actual_time():
    out = suggest(_cols(USUBJID=["A", "A"], NFRLT=["0", "1"], NTIM=["0", "1"]))
    assert "ACTUAL_TIME" not in _picked(out)
    q = next(q for q in out["needs_choice"] if q["role"] == "ACTUAL_TIME")
    assert all(c["confidence"] is None for c in q["choices"])


def test_values_lower_confidence_with_a_reason():
    good = suggest(_cols(USUBJID=["A", "A"], CONC=["1.5", "2"]))
    bad = suggest(_cols(USUBJID=["A", "A"], CONC=["high", "low"]))
    g = next(s for s in good["suggestions"] if s["role"] == "CONC")
    b = next(q for q in bad["needs_choice"] if q["role"] == "CONC")
    assert g["confidence"] > b["choices"][0]["confidence"]
    assert "not numbers" in b["choices"][0]["detail"]


@pytest.mark.parametrize(
    ("text", "kind", "unit"),
    [
        ("ng/ml", "conc", "ng/mL"),
        ("µg/mL", "conc", "ug/mL"),
        ("μg/L", "conc", "ug/L"),
        ("mcg/ml", "conc", "ug/mL"),
        (" MG ", "amount", "mg"),
        ("hours", "time", "h"),
        ("hr", "time", "h"),
        ("nmol/L", "conc", None),
        ("mg", "conc", None),
        ("IU", "amount", None),
        ("ng/mL", "time", None),
    ],
)
def test_units_are_spelling_only(text, kind, unit):
    assert normalise(text, kind) == unit


def test_header_unit_is_only_a_hint():
    assert header_hint("CONC (ng/ml)", "conc") == "ng/mL"
    assert header_hint("CONC", "conc") is None


@pytest.mark.parametrize(
    ("value", "route"),
    [
        ("extravascular", "extravascular"),
        ("EV", "extravascular"),
        ("Oral", "extravascular"),
        ("IV_Bolus", "iv-bolus"),
        ("intravenous infusion", "iv-infusion"),
        ("EXTRAVASULAR", None),
        ("SC", None),
        ("IM", None),
        ("INTRAVENOUS", None),
        ("", None),
    ],
)
def test_routes_follow_opnca(value, route):
    assert canonical_route(value) == route


def test_unrecognised_routes_are_listed_never_defaulted():
    listed = route_values(["extravascular", "extravascular", "EXTRAVASULAR", ""])
    assert listed == [
        {"value": "", "rows": 1, "route": None},
        {"value": "EXTRAVASULAR", "rows": 1, "route": None},
        {"value": "extravascular", "rows": 2, "route": "extravascular"},
    ]


def test_standard_mapping_has_no_problems():
    assert M.problems(standard_mapping()) == []


def _scenario(sid):
    return next(s for s in EXPECTED["scenarios"]["two_analyte.csv"] if s["id"] == sid)


@pytest.mark.parametrize("sid", ["dose_unmapped", "conc_unit_undeclared"])
def test_mapping_scenarios_block(sid):
    scenario = _scenario(sid)
    mapping = copy.deepcopy(standard_mapping())
    for role, column in scenario["change"].get("mapping", {}).items():
        mapping["roles"][role] = column
    for role, unit in scenario["change"].get("units", {}).items():
        if unit is None:
            mapping["units"].pop(role)
    got = {(p["check"], p["level"], p["role"]) for p in M.problems(mapping)}
    for inc in scenario["includes"]:
        assert (inc["check"], inc["level"], inc["role"]) in got


def test_missing_or_unknown_units_block():
    mapping = standard_mapping()
    mapping["units"]["ACTUAL_TIME"] = {"value": "fortnights"}
    assert [p["check"] for p in M.problems(mapping)] == ["units_undeclared"]


def test_flag_without_table_and_reused_columns_block():
    mapping = standard_mapping()
    del mapping["flags"]["MISSING"]
    mapping["roles"]["NOMINAL_TIME"] = "AFRLT"
    checks = {p["check"] for p in M.problems(mapping)}
    assert checks == {"flag_table_missing", "column_reused"}


# --- API -----------------------------------------------------------------------------


@pytest.fixture
def api(security, tmp_path):
    with nca_client(security, tmp_path, make_study(tmp_path)) as client:
        yield client


def test_mapping_needs_the_cleaned_data(api):
    doc = new_recipe(api)
    r = api.get(f"/api/nca/recipes/{doc['id']}/mapping-help")
    assert r.status_code == 404 and r.json()["detail"]["code"] == "not_ready"


def test_mapping_is_saved_in_the_recipe_and_hashed(api):
    doc = new_recipe(api)
    before = api.get(f"/api/nca/recipes/{doc['id']}").json()["sha256"]
    r = api.put(f"/api/nca/recipes/{doc['id']}/mapping", json={"mapping": standard_mapping()})
    assert r.status_code == 200, r.text
    assert r.json()["problems"] == [] and r.json()["sha256"] != before
    saved = api.get(f"/api/nca/recipes/{doc['id']}").json()["recipe"]
    assert saved["mapping"] == standard_mapping()


def test_mapping_to_a_missing_column_is_refused(api):
    doc = new_recipe(api)
    mapping = standard_mapping()
    mapping["roles"]["CONC"] = "NOPE"
    r = api.put(f"/api/nca/recipes/{doc['id']}/mapping", json={"mapping": mapping})
    assert r.status_code == 422 and "NOPE" in r.json()["detail"]["message"]


def test_a_mapping_change_keeps_the_cleaning_key(api):
    from opncafyr.workflows.nca.recipe import steps_hash

    doc = new_recipe(api)
    a = steps_hash(doc)
    doc["mapping"] = standard_mapping()
    assert steps_hash(doc) == a
