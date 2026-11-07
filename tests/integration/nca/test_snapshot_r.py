"""Exact-value analyte filter and frozen one-analyte snapshot (#25)."""

from __future__ import annotations

import csv
import hashlib
import json
import os

import pytest

from nca_helpers import (
    BLQ_CHOICE,
    EXPECTED,
    SOURCE,
    make_study,
    nca_client,
    ready_recipe,
    start_run,
)
from opncafyr.core.manifest import validate

pytestmark = pytest.mark.integration


@pytest.fixture
def env(security, tmp_path, runtime):
    study = make_study(tmp_path)
    with nca_client(security, tmp_path, study, runtime=runtime) as client:
        yield client, study, ready_recipe(client)


def _run_dir(study, run_id):
    return study / ".opncafyr" / "runs" / run_id


def _rows(study, run_id):
    with (_run_dir(study, run_id) / "input" / "analysis.csv").open(
        newline="", encoding="utf-8"
    ) as fh:
        return list(csv.DictReader(fh))


def test_each_snapshot_holds_only_its_analyte(env):
    api, study, rid = env
    parent = start_run(api, rid, "PARENT")
    metab = start_run(api, rid, "METAB")
    assert parent["run_id"] != metab["run_id"]
    p_rows, m_rows = _rows(study, parent["run_id"]), _rows(study, metab["run_id"])
    assert {r["PCTESTCD"] for r in p_rows} == {"PARENT"}
    assert {r["PCTESTCD"] for r in m_rows} == {"METAB"}
    assert {r["PCSPEC"] for r in p_rows + m_rows} == {"PLASMA"}
    # The planted overlap: the same subject and time in both analytes.
    keys = lambda rows: {(r["USUBJID"], r["AFRLT"]) for r in rows}  # noqa: E731
    assert len(keys(p_rows) & keys(m_rows)) > 0
    assert not {r["opncafyr_line"] for r in p_rows} & {r["opncafyr_line"] for r in m_rows}
    for run in (parent, metab):
        assert sorted(os.listdir(_run_dir(study, run["run_id"]))) == [
            "input",
            "logs",
            "manifest.json",
            "settings.json",
        ]


def test_whitespace_and_case_variants_are_never_merged(env):
    api, study, rid = env
    metab = start_run(api, rid, "METAB")
    variant = next(v for v in EXPECTED["files"][SOURCE]["analytes"]["values"] if v != v.strip())
    lines = {r["opncafyr_line"] for r in _rows(study, metab["run_id"])}
    variant_line = str(
        next(c for c in EXPECTED["cases"] if c["id"] == "analyte_value_variant")["lines"][0]
    )
    assert variant_line not in lines
    alone = start_run(api, rid, variant)
    assert [r["opncafyr_line"] for r in _rows(study, alone["run_id"])] == [variant_line]
    r = api.post(
        "/api/nca/runs",
        json={
            "recipe_id": rid,
            "analyte_value": "Parent",
            "specimen_value": "PLASMA",
            **BLQ_CHOICE,
        },
    )
    assert r.status_code == 409
    assert any(f["check"] == "analyte_no_rows" for f in r.json()["detail"]["findings"])


def test_snapshot_is_hashed_read_only_and_described(env):
    api, study, rid = env
    run = start_run(api, rid, "PARENT", analyte_label="Parent drug")
    folder = _run_dir(study, run["run_id"])
    data = (folder / "input" / "analysis.csv").read_bytes()
    assert run["analysis_input"]["sha256"] == hashlib.sha256(data).hexdigest()
    assert run["analysis_input"]["rows"] == len(_rows(study, run["run_id"]))
    for name in (
        "input/analysis.csv",
        "input/source-fingerprint.json",
        "input/opnca-mapping.json",
        "settings.json",
    ):
        assert not os.access(folder / name, os.W_OK), name
    validate(run, "run-manifest-v1")
    settings = json.loads((folder / "settings.json").read_text("utf-8"))
    validate(settings, "run-settings-v1")
    assert settings["analyte"] == {"column": "PCTESTCD", "value": "PARENT", "label": "Parent drug"}
    assert settings["blq"]["rules"] == BLQ_CHOICE["blq_rules"]
    fp = json.loads((folder / "input" / "source-fingerprint.json").read_text("utf-8"))
    assert set(fp) == {"path", "size_bytes", "sha256"}
    assert fp["sha256"] == hashlib.sha256((study / "source-data" / SOURCE).read_bytes()).hexdigest()


def test_acknowledged_warnings_are_recorded(env):
    api, study, rid = env
    run = start_run(api, rid, "PARENT")
    settings = json.loads((_run_dir(study, run["run_id"]) / "settings.json").read_text("utf-8"))
    checks = {a["check"] for a in settings["acknowledgements"]}
    assert checks == {"lloq_varies", "time_negative", "blq_above_lloq"}
    assert run["acknowledgements"] == settings["acknowledgements"]


def test_row_accounting_adds_up(env):
    api, study, rid = env
    acc = start_run(api, rid, "PARENT")["row_accounting"]
    assert acc["rows_source"] == EXPECTED["files"][SOURCE]["rows"]
    assert acc["rows_source"] == acc["rows_after_recipe"] + acc["rows_rejected_by_recipe"]
    assert acc["rows_after_recipe"] == acc["rows_analyte"] + acc["rows_other_analytes"]
    assert acc["rows_analyte"] == acc["rows_selected"] + acc["rows_other_specimens"]
    assert acc["rows_selected"] == acc["rows_snapshot"]
    assert (
        acc["rows_other_specimens"]
        == EXPECTED["files"][SOURCE]["analytes"]["values"]["PARENT"]["by_specimen"]["URINE"]
    )


def test_blq_rules_must_be_chosen(env):
    api, _, rid = env
    sel = {"analyte_value": "METAB", "specimen_value": "PLASMA"}
    keys = [
        f["key"]
        for f in api.post(f"/api/nca/recipes/{rid}/checks", json=sel).json()["findings"]
        if f["level"] == "warning"
    ]
    r = api.post("/api/nca/runs", json={"recipe_id": rid, **sel, "acknowledged": keys})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "needs_choice"


def test_runs_are_listed(env):
    api, _, rid = env
    run = start_run(api, rid, "METAB")
    listed = api.get("/api/nca/runs").json()["runs"]
    assert [r["run_id"] for r in listed] == [run["run_id"]]
    assert listed[0]["state"] == "preparing"
    assert api.get(f"/api/nca/runs/{run['run_id']}").json()["run"]["run_id"] == run["run_id"]
    assert api.get("/api/nca/runs/..%2F..").status_code == 404
