"""Mapping help on the cleaned two-analyte data (#23)."""

from __future__ import annotations

import pytest

from nca_helpers import EXPECTED, apply, make_study, nca_client, new_recipe

pytestmark = pytest.mark.integration


def test_mapping_help_on_cleaned_data(security, tmp_path, runtime):
    with nca_client(security, tmp_path, make_study(tmp_path), runtime=runtime) as api:
        doc = new_recipe(api)
        apply(api, doc["id"])
        help_ = api.get(f"/api/nca/recipes/{doc['id']}/mapping-help").json()
        picked = {s["role"]: s["column"] for s in help_["suggestions"]}
        standard = EXPECTED["standard_setup"]["mapping"]
        assert {r: picked.get(r) for r in standard} == standard
        # Nothing is saved until the user confirms it.
        assert help_["mapping"] == {"roles": {}}
        assert {p["role"] for p in help_["problems"]} == {
            "SUBJECT_ID",
            "ANALYTE",
            "ACTUAL_TIME",
            "CONC",
            "DOSE",
            "ROUTE",
        }
        blq = next(c for c in help_["columns"] if c["column"] == "BLQFL")
        assert {v["value"] for v in blq["values"]} == {"Y", "", "N"}

        api.put(
            f"/api/nca/recipes/{doc['id']}/mapping", json={"mapping": {"roles": {"ROUTE": "ROUTE"}}}
        ).raise_for_status()
        routes = api.get(f"/api/nca/recipes/{doc['id']}/mapping-help").json()["routes"]
        unknown = {r["value"] for r in routes if r["route"] is None}
        assert unknown == {"", "EXTRAVASULAR"}
