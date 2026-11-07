"""Checks through the API on R-cleaned data, recipe scenarios and the run gate (#24)."""

from __future__ import annotations

import json

import pytest

from nca_helpers import (
    EXPECTED,
    SOURCE,
    apply,
    make_study,
    nca_client,
    new_recipe,
    project,
    resolving_mapping,
    resolving_steps,
    standard_mapping,
    wait_job,
)

pytestmark = pytest.mark.integration
SPECIMEN = EXPECTED["standard_setup"]["specimen_value"]
SCENARIOS = {s["id"]: s for s in EXPECTED["scenarios"][SOURCE]}


@pytest.fixture
def api(security, tmp_path, runtime):
    with nca_client(security, tmp_path, make_study(tmp_path), runtime=runtime) as client:
        yield client


def _ready(api, steps=None, mapping=None):
    doc = new_recipe(api, steps=steps)
    apply(api, doc["id"])
    r = api.put(
        f"/api/nca/recipes/{doc['id']}/mapping", json={"mapping": mapping or standard_mapping()}
    )
    assert r.status_code == 200, r.text
    return doc["id"]


def _checks(api, rid, analyte, specimen=SPECIMEN):
    r = api.post(
        f"/api/nca/recipes/{rid}/checks",
        json={"analyte_value": analyte, "specimen_value": specimen},
    )
    assert r.status_code == 200, r.text
    return r.json()


def test_api_findings_match_the_fixture(api):
    rid = _ready(api)
    got = []
    for analyte in ("PARENT", "METAB"):
        got += [f for f in _checks(api, rid, analyte)["findings"] if f["level"] != "info"]
    canon = sorted(json.dumps(project(f), sort_keys=True) for f in got)
    expected = sorted(
        json.dumps(f, sort_keys=True) for f in EXPECTED["findings"][SOURCE] if f["level"] != "info"
    )
    assert canon == expected
    assert all("key" in f for f in got if f["level"] == "warning")


def test_analyte_values_are_listed_exactly(api):
    rid = _ready(api)
    values = {v["value"]: v for v in api.get(f"/api/nca/recipes/{rid}/analytes").json()["values"]}
    expected = EXPECTED["files"][SOURCE]["analytes"]["values"]
    assert set(values) == set(expected)
    for value, facts in expected.items():
        assert values[value]["rows"] == facts["rows"]
        assert values[value]["specimens"] == facts["by_specimen"]


def test_opnca_format_check_adds_info_findings(api):
    rid = _ready(api)
    out = _checks(api, rid, "PARENT")
    job = wait_job(api, out["format"]["job_id"])
    assert job["state"] == "completed", job["message"]
    more = api.post(
        f"/api/nca/recipes/{rid}/format-check",
        json={"analyte_value": "PARENT", "specimen_value": SPECIMEN},
    ).json()
    assert more["findings"] and all(f["level"] == "info" for f in more["findings"])
    assert all(f["message"].startswith("opnca-r ") for f in more["findings"])
    # Second time: from the stored report, no new job.
    again = _checks(api, rid, "PARENT")
    assert again["format"]["job_id"] is None
    assert [f for f in again["findings"] if f["check"] == "opnca_format_check"]


@pytest.mark.parametrize("sid", ["derive_elapsed_time", "analyte_code_trimmed"])
def test_recipe_scenarios(api, sid):
    scenario = SCENARIOS[sid]
    mapping = standard_mapping()
    mapping["roles"].update(scenario["change"].get("mapping", {}))
    rid = _ready(api, steps=scenario["change"]["recipe"], mapping=mapping)
    found = []
    for analyte in scenario["analytes"]:
        found += _checks(api, rid, analyte)["findings"]
    for inc in scenario["includes"]:
        assert any(all(f.get(k) == v for k, v in inc.items()) for f in found), inc


def test_run_is_refused_while_anything_blocks(api):
    rid = _ready(api)
    body = {"recipe_id": rid, "analyte_value": "PARENT", "specimen_value": SPECIMEN}
    r = api.post("/api/nca/runs", json=body)
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert detail["code"] == "checks_blocking"
    assert detail["findings"] and all(f["level"] == "blocking" for f in detail["findings"])


def test_resolved_data_needs_every_warning_acknowledged(api):
    rid = _ready(api, steps=resolving_steps(), mapping=resolving_mapping())
    for analyte in ("PARENT", "METAB"):
        out = _checks(api, rid, analyte)
        assert out["summary"]["blocking"] == 0, [
            project(f) for f in out["findings"] if f["level"] == "blocking"
        ]
        body = {"recipe_id": rid, "analyte_value": analyte, "specimen_value": SPECIMEN}
        r = api.post("/api/nca/runs", json=body)
        assert r.status_code == 409 and r.json()["detail"]["code"] == "warnings_unacknowledged"
