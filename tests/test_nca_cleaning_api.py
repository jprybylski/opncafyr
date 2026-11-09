"""Cleaning recipes through the API, without R (#22). R runs in tests/integration/nca."""

from __future__ import annotations

import copy

import pytest

from nca_helpers import SOURCE, make_study, nca_client, new_recipe


@pytest.fixture
def api(security, tmp_path):
    study = make_study(tmp_path)
    with nca_client(security, tmp_path, study) as client:
        client.study = study
        yield client


def test_workflow_view_is_listed_and_served(api):
    packs = api.get("/api/workflows").json()["workflows"]
    nca = next(p for p in packs if p["id"] == "nca")
    view = nca["views"][0]
    assert view["id"] == "analysis" and view["module"] == "static/workflows/nca/analysis.js"
    r = api.get("/" + view["module"])
    assert r.status_code == 200 and "export function init" in r.text
    assert api.get("/static/workflows/nca/analysis.css").status_code == 200


def test_recipe_lifecycle(api):
    doc = new_recipe(api)
    assert doc["steps"] == [] and doc["schema_version"] == "1.0"
    got = api.get(f"/api/nca/recipes/{doc['id']}").json()
    assert got["columns"][0] == "opncafyr_line" and "AFRLT" in got["header"]
    doc["steps"] = [{"op": "clean_names", "args": {}}]
    saved = api.put(f"/api/nca/recipes/{doc['id']}", json={"recipe": doc}).json()
    assert "afrlt" in saved["columns"]
    listed = api.get("/api/nca/recipes").json()["recipes"]
    assert [r["id"] for r in listed] == [doc["id"]] and listed[0]["steps"] == 1


def test_invalid_recipe_is_refused_with_its_step(api):
    doc = new_recipe(api)
    doc["steps"] = [
        {"op": "trim", "args": {"columns": ["PCTESTCD"]}},
        {"op": "recode", "args": {"column": "CONC", "map": {"<LLOQ": "0"}}},
    ]
    r = api.put(f"/api/nca/recipes/{doc['id']}", json={"recipe": doc})
    assert r.status_code == 422
    assert r.json()["detail"]["step"] == 2
    # Nothing was saved.
    assert api.get(f"/api/nca/recipes/{doc['id']}").json()["recipe"]["steps"] == []


def test_recipe_stays_with_its_source_and_id(api):
    doc = new_recipe(api)
    other = dict(doc, source_id="something-else")
    assert api.put(f"/api/nca/recipes/{doc['id']}", json={"recipe": other}).status_code == 422
    renamed = dict(doc, id="another")
    assert api.put(f"/api/nca/recipes/{doc['id']}", json={"recipe": renamed}).status_code == 422


def test_recipe_ids_are_checked_in_the_path(api):
    assert api.get("/api/nca/recipes/..%2Fworkspace").status_code in (404, 422)
    assert api.get("/api/nca/recipes/UPPER").status_code == 422


def test_put_needs_the_session_secret(api):
    doc = new_recipe(api)
    r = api.put(
        f"/api/nca/recipes/{doc['id']}",
        json={"recipe": doc},
        headers={"x-opncafyr-session": "wrong"},
    )
    assert r.status_code == 403


def test_apply_without_r_fails_as_a_job(api):
    doc = new_recipe(api)
    started = api.post(f"/api/nca/recipes/{doc['id']}/apply").json()
    from nca_helpers import wait_job

    job = wait_job(api, started["job_id"])
    assert job["state"] == "failed" and "R is not available" in job["message"]
    assert api.get(f"/api/nca/recipes/{doc['id']}/result").status_code == 404


def test_result_before_apply_is_not_ready(api):
    doc = new_recipe(api)
    r = api.get(f"/api/nca/recipes/{doc['id']}/result", params={"kind": "preview"})
    assert r.status_code == 404 and r.json()["detail"]["code"] == "not_ready"


STEPS = [
    {"op": "trim", "args": {"columns": ["PCTESTCD", "PCSPEC"]}, "note": "stray spaces"},
    {
        "op": "exclude",
        "args": {"where": {"column": "PCSPEC", "equals": "URINE"}, "reason": "Not plasma"},
        "note": "plasma only",
    },
    {"op": "report_dupes", "args": {"columns": ["USUBJID", "PCTESTCD", "AFRLT"]}},
]


def _put(api, doc):
    return api.put(f"/api/nca/recipes/{doc['id']}", json={"recipe": doc})


def test_editing_a_step_replaces_it_in_place_and_changes_the_hash(api):
    # #63: Edit saves the whole recipe with one step replaced. The order, the other steps and
    # their notes stay; the hash changes, which is what makes cleaning, checks and
    # acknowledgements stale; the source file is not touched.
    doc = new_recipe(api, steps=STEPS)
    source = api.study / "source-data" / SOURCE
    source_bytes = source.read_bytes()
    before = api.get(f"/api/nca/recipes/{doc['id']}").json()

    edited = copy.deepcopy(before["recipe"])
    edited["steps"][1] = {
        "op": "exclude",
        "args": {"where": {"column": "PCSPEC", "in": ["URINE", "SERUM"]}, "reason": "Not plasma"},
        "note": "plasma only, revised",
    }
    r = _put(api, edited)
    assert r.status_code == 200, r.text
    after = r.json()
    assert after["sha256"] != before["sha256"]
    steps = after["recipe"]["steps"]
    assert [s["op"] for s in steps] == ["trim", "exclude", "report_dupes"]
    assert steps[1]["args"]["where"] == {"column": "PCSPEC", "in": ["URINE", "SERUM"]}
    assert steps[1]["note"] == "plasma only, revised"
    assert steps[0] == STEPS[0] and steps[2] == STEPS[2]  # the others are as they were
    again = api.get(f"/api/nca/recipes/{doc['id']}").json()
    assert again["sha256"] == after["sha256"] and again["recipe"]["steps"] == steps
    assert source.read_bytes() == source_bytes


def test_an_invalid_edit_is_refused_with_its_step_and_changes_nothing(api):
    doc = new_recipe(api, steps=STEPS)
    before = api.get(f"/api/nca/recipes/{doc['id']}").json()
    bad = copy.deepcopy(before["recipe"])
    bad["steps"][1]["args"]["where"] = {"column": "NO_SUCH_COLUMN", "equals": "x"}
    r = _put(api, bad)
    assert r.status_code == 422
    assert r.json()["detail"]["step"] == 2 and r.json()["detail"]["code"] == "recipe_invalid"
    now = api.get(f"/api/nca/recipes/{doc['id']}").json()
    assert now["sha256"] == before["sha256"] and now["recipe"]["steps"] == before["recipe"]["steps"]


def test_saving_an_unchanged_step_keeps_the_hash(api):
    # Edit then Save without changing anything must not make the checks stale.
    doc = new_recipe(api, steps=STEPS)
    before = api.get(f"/api/nca/recipes/{doc['id']}").json()
    r = _put(api, before["recipe"])
    assert r.status_code == 200 and r.json()["sha256"] == before["sha256"]
