"""Run bundles on real runs: verify and rerun (#26)."""

from __future__ import annotations

import stat

import pytest

from nca_helpers import make_study, nca_client, ready_recipe, start_run

pytestmark = pytest.mark.integration


def test_verify_and_rerun(security, tmp_path, runtime):
    study = make_study(tmp_path)
    with nca_client(security, tmp_path, study, runtime=runtime) as api:
        rid = ready_recipe(api)
        run = start_run(api, rid, "PARENT")
        report = api.get(f"/api/runs/{run['run_id']}/verify").json()["report"]
        assert report["ok"] is True and report["sealed"] is False

        again = api.post(f"/api/nca/runs/{run['run_id']}/rerun")
        assert again.status_code == 201, again.text
        child = again.json()["run"]
        assert child["run_id"] != run["run_id"]
        assert child["lineage"]["parent_run_id"] == run["run_id"]
        assert child["analysis_input"]["sha256"] == run["analysis_input"]["sha256"]
        assert child["acknowledgements"] and child["nca_settings"] == run["nca_settings"]

        snapshot = study / ".opncafyr" / "runs" / run["run_id"] / "input" / "analysis.csv"
        snapshot.chmod(stat.S_IWUSR | stat.S_IRUSR)
        snapshot.write_text(snapshot.read_text("utf-8") + "\n", "utf-8")
        report = api.get(f"/api/runs/{run['run_id']}/verify").json()["report"]
        assert report["ok"] is False and report["changed"] == ["input/analysis.csv"]
        assert api.get("/api/runs/nope/verify").status_code == 404


def test_a_run_records_the_profile_step_only_when_chosen(security, tmp_path, runtime):
    # Study workflow profiles (#68): provenance only, written when the run is made.
    study = make_study(tmp_path)
    (study / "profiles").mkdir()
    (study / "profiles" / "periods.yaml").write_text(
        'schema_version: "1.0"\nid: periods\nversion: "1"\ntitle: Periods\nsteps:\n'
        "  - id: run-a\n    type: run\n    title: Period A\n    slot: a\n",
        "utf-8",
    )
    with nca_client(security, tmp_path, study, runtime=runtime) as api:
        rid = ready_recipe(api)
        plain = start_run(api, rid, "PARENT")
        assert "workflow_context" not in plain
        api.post("/api/nca/profiles/select", json={"name": "periods.yaml"}).raise_for_status()
        run = start_run(api, rid, "PARENT", profile_step="run-a")
        context = run["workflow_context"]
        assert context["profile_path"] == "profiles/periods.yaml"
        assert context["step_id"] == "run-a" and context["profile_id"] == "periods"
        # The analysis itself is the same with or without a profile step.
        assert run["analysis_input"]["sha256"] == plain["analysis_input"]["sha256"]
        assert run["nca_settings"] == plain["nca_settings"]
