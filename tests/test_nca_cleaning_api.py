"""Cleaning recipes through the API, without R (#22). R runs in tests/integration/nca."""

from __future__ import annotations

import pytest

from nca_helpers import make_study, nca_client, new_recipe


@pytest.fixture
def api(security, tmp_path):
    study = make_study(tmp_path)
    with nca_client(security, tmp_path, study) as client:
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
