"""Replay verification of saved runs, and the Gate 2 exit test (#29)."""

from __future__ import annotations

import csv
import json
import stat

import pytest

from nca_helpers import (
    SOURCE,
    make_study,
    nca_client,
    nca_supervisor,
    ready_recipe,
    review_and_replay,
    start_run,
    wait_job,
)
from opncafyr.core import runs as R
from opncafyr.core.manifest import validate

pytestmark = pytest.mark.integration


@pytest.fixture
def env(security, tmp_path, runtime):
    study = make_study(tmp_path)
    sup = nca_supervisor(tmp_path, runtime)
    with nca_client(security, tmp_path, study, runtime=runtime, supervisor=sup) as api:
        yield api, study, ready_recipe(api)


def _folder(study, run_id):
    return study / ".opncafyr" / "runs" / run_id


def _csv(path):
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def test_a_saved_run_replays_and_completes(env):
    api, study, rid = env
    run_id = start_run(api, rid, "PARENT")["run_id"]
    out = review_and_replay(api, study, run_id)
    replay = out["run"]["replay"]
    assert replay["status"] == "passed" and replay["rtol"] == 1e-10
    report = json.loads((_folder(study, run_id) / replay["path"]).read_text("utf-8"))
    validate(report, "replay-v1")
    assert report["tables"] and all(t["ok"] for t in report["tables"])
    done = api.post(f"/api/nca/runs/{run_id}/complete")
    assert done.status_code == 200, done.text
    sealed = done.json()["run"]
    assert sealed["state"] == "completed"
    assert replay["path"] in {f["path"] for f in sealed["sealed"]["files"]}
    assert R.verify_run(_folder(study, run_id))["ok"] is True


def test_an_altered_result_fails_the_replay_with_a_readable_diff(env):
    api, study, rid = env
    run_id = start_run(api, rid, "METAB")["run_id"]
    review_and_replay(api, study, run_id)
    table = _folder(study, run_id) / "results" / "opnca-parameters-long.csv"
    rows = _csv(table)
    target = next(r for r in rows if r["value"] not in ("", "NA"))
    target["value"] = str(float(target["value"]) * 1.001)
    table.chmod(stat.S_IRUSR | stat.S_IWUSR)
    with table.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    job = api.post(f"/api/nca/runs/{run_id}/replay", json={}).json()["job_id"]
    assert wait_job(api, job, timeout=600)["state"] == "completed"
    out = api.post(f"/api/nca/runs/{run_id}/save-status").json()
    assert out["run"]["replay"]["status"] == "failed"
    assert "opnca-parameters-long.csv row" in out["replay"]["differences"]
    assert ", value: saved" in out["replay"]["differences"]
    r = api.post(f"/api/nca/runs/{run_id}/complete")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "replay_required"


# Since opnca-r 0.3.0 the API writes numbers unrounded (15 significant digits), so the
# page's values match the replay to the CSVs' own tolerance.
API_RTOL = 1e-10


def test_replay_matches_opncas_own_calculation(env):
    api, study, rid = env
    run_id = start_run(api, rid, "PARENT")["run_id"]
    out = review_and_replay(api, study, run_id)
    ui = {
        (r["profile_id"], r["metric"]): r
        for r in out["calculate"]["results"]
        if r.get("estimand", "observed_only") == "observed_only"
    }
    replay_dir = (_folder(study, run_id) / out["run"]["replay"]["path"]).parent / "replay"
    replayed = _csv(replay_dir / "opnca-parameters-long.csv")
    assert replayed and len(replayed) == len(ui)
    for row in replayed:
        calc = ui[(row["profile_id"], row["metric"])]
        assert row["status"] == calc["status"]
        if calc["value"] is None:
            assert row["value"] in ("", "NA")
        else:
            a, b = float(row["value"]), float(calc["value"])
            assert abs(a - b) <= API_RTOL * max(abs(a), abs(b)), (row, calc)


def test_gate_2_two_analytes_two_independent_runs(env):
    """docs/plan.md Gate 2: a two-analyte fixture yields two distinct scripts, snapshots,
    manifests and outputs, with no cross-analyte rows, and both replay."""
    api, study, rid = env
    runs = {}
    for analyte in ("PARENT", "METAB"):
        run_id = start_run(api, rid, analyte)["run_id"]
        review_and_replay(api, study, run_id)
        done = api.post(f"/api/nca/runs/{run_id}/complete")
        assert done.status_code == 200, done.text
        runs[analyte] = done.json()["run"]
    parent, metab = runs["PARENT"], runs["METAB"]
    assert parent["run_id"] != metab["run_id"]
    for key in ("analysis_input", "script", "record", "settings"):
        assert parent[key]["sha256"] != metab[key]["sha256"], key
    assert parent["export"]["artifacts_sha256"] != metab["export"]["artifacts_sha256"]
    for analyte, run in runs.items():
        folder = _folder(study, run["run_id"])
        validate(run, "run-manifest-v1")
        assert run["state"] == "completed" and run["replay"]["status"] == "passed"
        assert run["source"]["path"] == f"source-data/{SOURCE}"
        assert {r["PCTESTCD"] for r in _csv(folder / "input" / "analysis.csv")} == {analyte}
        assert R.verify_run(folder)["ok"] is True
        record = json.loads((folder / "analysis-record.json").read_text("utf-8"))
        assert record["data"]["sha256"] == run["analysis_input"]["sha256"]
        assert record["context"]["analyte"] == analyte
        # Every profile in the results is a subject of this analyte's own snapshot.
        subjects = {r["USUBJID"] for r in _csv(folder / "input" / "analysis.csv")}
        long = _csv(folder / "results" / "opnca-parameters-long.csv")
        assert {r["profile_id"] for r in long} <= subjects
        # A profile excluded in full (the no-dose subject) gets no parameter values.
        snapshot = _csv(folder / "input" / "analysis.csv")
        excluded = {
            s
            for s in subjects
            if all(r["NCA_EXCLUDED"] == "TRUE" for r in snapshot if r["USUBJID"] == s)
        }
        assert excluded
        assert all(r["value"] in ("", "NA") for r in long if r["profile_id"] in excluded)
    listed = {r["run_id"]: r["state"] for r in api.get("/api/nca/runs").json()["runs"]}
    assert listed == {parent["run_id"]: "completed", metab["run_id"]: "completed"}
