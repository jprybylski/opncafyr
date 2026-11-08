"""opnca-r's server-side save into the run folder, checked and accepted (#28)."""

from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess

import pytest

from nca_helpers import (
    make_study,
    nca_client,
    nca_supervisor,
    preset_of,
    ready_recipe,
    same_origin,
    save_in_opnca,
    save_status,
    start_run,
    wait_job,
)
from opncafyr.core import runs as R
from opncafyr.core.manifest import validate
from opncafyr.workflows.nca.publish.catalog import catalog, missing
from publish_helpers import png

pytestmark = pytest.mark.integration


@pytest.fixture
def env(security, tmp_path, runtime):
    study = make_study(tmp_path)
    sup = nca_supervisor(tmp_path, runtime)
    with nca_client(security, tmp_path, study, runtime=runtime, supervisor=sup) as api:
        yield api, study, ready_recipe(api), runtime


def _folder(study, run_id):
    return study / ".opncafyr" / "runs" / run_id


def test_a_save_moves_the_run_to_review_with_every_artifact(env):
    api, study, rid, _ = env
    run_id = start_run(api, rid, "PARENT")["run_id"]
    assert save_status(api, run_id)["save"]["state"] == "none"
    save_in_opnca(api, study, run_id)
    out = save_status(api, run_id)
    assert out["save"] == {"state": "complete", "problems": []}, out
    run = out["run"]
    assert run["state"] == "reviewing"
    validate(run, "run-manifest-v1")
    # The session was stopped once the save was complete.
    assert api.get("/api/nca/session").json()["session"]["run_id"] is None
    listing = json.loads((_folder(study, run_id) / "artifacts.json").read_text("utf-8"))
    assert {a["path"] for a in run["artifacts"]} == {a["path"] for a in listing["artifacts"]}
    for art in run["artifacts"]:
        assert art["sha256"] == R.sha256_file(_folder(study, run_id) / art["path"])
        # results/tables/*.rds: opnca-r's flextable copies of the CSVs (opnca-r#54).
        rds = art["path"].startswith("results/tables/") and art["path"].endswith(".rds")
        kinds = ("other",) if rds else ("script", "record", "table", "figure")
        assert art["media_type"] and art["kind"] in kinds
    assert run["tools"]["opnca-r"] == "0.3.1" and run["tools"]["R"].startswith("R version")
    assert run["nca_settings"]["effective_settings"]
    # The labels opnca-r used are the run's, set by the preset (#57, opnca-r#56).
    assert run["nca_settings"]["effective_settings"]["units"] == run["analysis_units"]
    assert run["nca_settings"]["effective_settings"]["units_source"] == "preset"
    assert run["nca_settings"]["settings"]["blq_rules"] == run["nca_settings"]["blq"]["rules"]
    assert run["exclusions"]["launcher"]["rows"] > 0 and run["exclusions"]["session"] == []
    assert R.verify_run(_folder(study, run_id))["ok"] is True


def test_analysis_r_runs_standalone_from_a_copy(env, tmp_path):
    api, study, rid, runtime = env
    run_id = start_run(api, rid, "METAB")["run_id"]
    save_in_opnca(api, study, run_id)
    assert save_status(api, run_id)["run"]["state"] == "reviewing"
    src = _folder(study, run_id)
    script = (src / "analysis.R").read_text("utf-8")
    assert 'read.csv("input/analysis.csv"' in script
    copy = tmp_path / "replay-copy"
    (copy / "input").mkdir(parents=True)
    shutil.copyfile(src / "analysis.R", copy / "analysis.R")
    shutil.copyfile(src / "input" / "analysis.csv", copy / "input" / "analysis.csv")
    env_vars = {k: v for k, v in os.environ.items() if not k.startswith("OPNCAFYR_")}
    env_vars["R_LIBS"] = str(runtime.r_library)
    subprocess.run(
        [str(runtime.executable("rscript")), "--vanilla", "analysis.R"],
        cwd=copy,
        env=env_vars,
        check=True,
        capture_output=True,
    )
    assert (copy / "replay" / "opnca-parameters-long.csv").is_file()


def test_completion_needs_a_reviewed_and_replayed_run(env):
    api, study, rid, _ = env
    run_id = start_run(api, rid, "PARENT")["run_id"]
    r = api.post(f"/api/nca/runs/{run_id}/complete")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "bad_state"
    save_in_opnca(api, study, run_id)
    save_status(api, run_id)
    r = api.post(f"/api/nca/runs/{run_id}/complete")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "replay_required"
    assert api.post(f"/api/nca/runs/{run_id}/session").status_code == 409


def test_an_interrupted_save_never_completes(env):
    api, study, rid, _ = env
    run_id = start_run(api, rid, "METAB")["run_id"]
    save_in_opnca(api, study, run_id)
    # As a kill between upstream's renames would leave it: files without COMPLETE.
    (_folder(study, run_id) / "COMPLETE").unlink()
    shutil.rmtree(_folder(study, run_id) / "results")
    out = save_status(api, run_id)
    assert out["save"]["state"] == "failed"
    assert out["run"]["state"] == "failed" and out["run"]["failure"]["reason"] == "save_failed"
    assert api.post(f"/api/nca/runs/{run_id}/complete").status_code == 409
    again = api.post(f"/api/nca/runs/{run_id}/rerun")
    assert again.status_code == 201


def test_session_exclusions_add_to_the_launchers(env):
    api, study, rid, _ = env
    run_id = start_run(api, rid, "PARENT")["run_id"]
    preset = preset_of(study, run_id)
    subject, time = _first_point(study, run_id)

    def exclude_a_point(base):
        r = api.post(
            base + "dataset/exclude",
            headers=same_origin(api),
            json={
                "id": preset["id"],
                "time": preset["time"],
                "source": "test",
                "point_ids": [subject],
                "point_times": [time],
            },
        )
        assert r.status_code == 200, r.text

    # The page maps the excluded role to OPNCA_EXCLUDED once a point is excluded; since
    # opnca-r 0.2.9 that column starts from the launcher's NCA_EXCLUDED rows (opnca-r#49).
    save_in_opnca(
        api,
        study,
        run_id,
        body_extra={"excluded": "OPNCA_EXCLUDED"},
        before_save=exclude_a_point,
    )
    out = save_status(api, run_id)
    assert out["save"]["state"] == "complete", out
    run = out["run"]
    assert run["nca_settings"]["mapping"]["excluded"] == "NCA_EXCLUDED"
    assert run["exclusions"]["launcher"]["rows"] > 0
    assert [(o["id"], o["time"], o["source"]) for o in run["exclusions"]["session"]] == [
        (subject, time, "test")
    ]
    assert any(w.startswith("session_exclusion_no_reason: 1 ") for w in run["warnings"])
    # analysis.R rebuilds both sets, so the replay matches.
    job = wait_job(api, api.app.state.nca.replays[run_id], timeout=600)
    assert job["state"] == "completed", job["message"]
    assert save_status(api, run_id)["run"]["replay"]["status"] == "passed"


def test_the_mean_figures_are_saved_with_their_choices_and_replayed(env):
    api, study, rid, _ = env
    run_id = start_run(api, rid, "PARENT")["run_id"]
    figure = base64.b64encode(png("mean")).decode()
    save = {
        "figure_names": ["mean-linear", "mean-log"],
        "figure_formats": ["png", "png"],
        "figure_data": [figure, figure],
    }
    # opnca-r refuses the mean without a time basis; it never picks one (opnca-r#50).
    with pytest.raises(AssertionError, match="MEAN_SETTINGS_REQUIRED"):
        save_in_opnca(api, study, run_id, save_extra=save)
    save_in_opnca(api, study, run_id, save_extra={**save, "time_basis": "actual"}, reopen=False)
    run = save_status(api, run_id)["run"]
    assert run["state"] == "reviewing", run
    paths = {a["path"] for a in run["artifacts"]}
    assert {
        "results/opnca-mean-profiles.csv",
        "results/figures/mean-linear.png",
        "results/figures/mean-log.png",
    } <= paths
    mean = run["nca_settings"]["mean_profiles"]
    assert (mean["time_basis"], mean["statistic"], mean["error_measure"]) == (
        "actual",
        "arithmetic",
        "sd",
    )
    job = wait_job(api, api.app.state.nca.replays[run_id], timeout=600)
    assert job["state"] == "completed", job["message"]
    run = save_status(api, run_id)["run"]
    assert run["replay"]["status"] == "passed"
    entries = {e.artifact: e for e in catalog(run)}
    assert entries["mean-linear"].preselected and entries["mean-profiles"].note.startswith(
        "Arithmetic mean ± SD at each actual time"
    )
    assert missing(run) == []


def _first_point(study, run_id):
    import csv

    path = _folder(study, run_id) / "input" / "analysis.csv"
    with path.open(newline="", encoding="utf-8") as fh:
        row = next(r for r in csv.DictReader(fh) if r["NCA_EXCLUDED"] == "FALSE")
    return row["USUBJID"], float(row["AFRLT"])
