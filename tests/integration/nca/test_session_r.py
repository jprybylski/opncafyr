"""opnca-r sessions on one-analyte snapshots, through the proxy (#27)."""

from __future__ import annotations

import csv
import json
import shutil

import pytest

from nca_helpers import (
    make_study,
    nca_client,
    nca_supervisor,
    ready_recipe,
    same_origin,
    start_run,
)
from process_helpers import tree_pids, wait_gone

pytestmark = pytest.mark.integration
API = "/apps/opnca/api/v1/"
METRICS = ["cmax", "tmax", "auclast", "lambda_z", "half_life"]


def _snapshot(study, run_id):
    folder = study / ".opncafyr" / "runs" / run_id
    with (folder / "input" / "analysis.csv").open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    preset = json.loads((folder / "input" / "opnca-mapping.json").read_text("utf-8"))
    return folder, rows, preset


def _open(api, run_id):
    r = api.post(f"/api/nca/runs/{run_id}/session")
    assert r.status_code == 200, r.text
    assert r.json()["session"] == {"run_id": run_id, "state": "running"}


def _calculate(api, preset):
    r = api.post(
        API + "analysis/calculate", json={**preset, "metrics": METRICS}, headers=same_origin(api)
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["results"]


def _child_pids(api):
    return tree_pids(api.app.state.supervisor.get("opnca").pid)


@pytest.fixture
def env(security, tmp_path, runtime):
    study = make_study(tmp_path)
    sup = nca_supervisor(tmp_path, runtime)
    with nca_client(security, tmp_path, study, runtime=runtime, supervisor=sup) as api:
        rid = ready_recipe(api)
        parent = start_run(api, rid, "PARENT")["run_id"]
        metab = start_run(api, rid, "METAB")["run_id"]
        yield api, study, parent, metab


def test_each_run_shows_only_its_analyte_and_its_own_mapping(env):
    api, study, parent, metab = env
    for run_id, analyte in ((parent, "PARENT"), (metab, "METAB")):
        _, rows, preset = _snapshot(study, run_id)
        _open(api, run_id)
        assert api.get(f"/api/nca/runs/{run_id}").json()["run"]["state"] == "running"
        meta = api.get(API + "meta").json()["data"]
        config = meta["config"]
        assert config["preset_mapping"] == preset
        assert config["storage_namespace"] == run_id
        assert config["persist_mapping"] is False and config["guess_roles"] is False
        assert config["require_explicit_settings"] is True and config["lock_dataset"] is True
        assert config["allow_overwrite"] is False and config["export_enabled"] is True
        fp = api.get(API + "dataset/fingerprint").json()["data"]
        assert fp["n_rows"] == len(rows)
        preview = api.get(API + "dataset/preview").json()["data"]["preview"]
        codes = {r["PCTESTCD"] for r in preview}
        assert codes == {analyte}
        # A no-dose profile excluded with a reason, and NA dose, are accepted.
        assert _calculate(api, preset)


def test_switching_runs_and_stopping_leave_no_r_process(env):
    api, _, parent, metab = env
    _open(api, parent)
    first = _child_pids(api)
    assert first
    _open(api, metab)
    assert wait_gone(first) == []
    second = _child_pids(api)
    r = api.delete(f"/api/nca/runs/{metab}/session")
    assert r.json()["session"]["run_id"] is None
    assert wait_gone(second) == []
    _open(api, parent)
    third = _child_pids(api)
    api.app.state.supervisor.stop_all()  # what the launcher's shutdown runs
    assert wait_gone(third) == []


def test_a_saved_or_completed_run_is_not_reopened(env):
    api, study, parent, _ = env
    (study / ".opncafyr" / "runs" / parent / "COMPLETE").write_text("{}", "utf-8")
    r = api.post(f"/api/nca/runs/{parent}/session")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "run_saved"


def test_opnca_ignores_conc_on_blq_rows(env, tmp_path):
    """ADR 0005 "still to confirm": a number in CONC on a BLQ row must not change results."""
    api, study, parent, _ = env
    folder, rows, preset = _snapshot(study, parent)
    _open(api, parent)
    base = _calculate(api, preset)
    blq = [r for r in rows if r["NCA_BLQ"] == "TRUE"]
    assert blq
    copy = tmp_path / "copy" / "20261107T000000Z-beef"
    (copy / "input").mkdir(parents=True)
    fields = list(rows[0])
    with (copy / "input" / "analysis.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fields, lineterminator="\n")
        writer.writeheader()
        for r in rows:
            writer.writerow({**r, "NCA_CONC": "999" if r["NCA_BLQ"] == "TRUE" else r["NCA_CONC"]})
    shutil.copyfile(folder / "input" / "opnca-mapping.json", copy / "input" / "opnca-mapping.json")
    api.app.state.supervisor.start(
        "opnca",
        {
            "data_path": str(copy / "input" / "analysis.csv"),
            "output_dir": str(copy),
            "run_id": copy.name,
            "mapping_path": str(copy / "input" / "opnca-mapping.json"),
        },
    )
    assert _calculate(api, preset) == base
