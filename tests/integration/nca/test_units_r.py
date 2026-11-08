"""Unit labels from the launcher to opnca-r and back (#57, opnca-r#56)."""

from __future__ import annotations

import csv
import json

import pytest

from nca_helpers import (
    make_study,
    nca_client,
    nca_supervisor,
    ready_recipe,
    resolving_steps,
    review_and_replay,
    start_run,
)

pytestmark = pytest.mark.integration

DEFAULT = {"concentration": "ng/mL", "time": "h", "dose": "mg"}
# ng/mL to ug/L has the factor 1: the step is recorded and changes the unit column, so
# only the labels may change, never an estimate.
CONVERT = {
    "op": "convert_units",
    "args": {"value_column": "CONC", "unit_column": "PCSTRESU", "from": "ng/mL", "to": "ug/L"},
}


def _long(study, run_id):
    path = study / ".opncafyr" / "runs" / run_id / "results" / "opnca-parameters-long.csv"
    with path.open(newline="", encoding="utf-8") as fh:
        return {
            (r["profile_id"], r["metric"], r.get("estimand", "")): r for r in csv.DictReader(fh)
        }


def test_a_non_default_unit_system_labels_the_results_and_keeps_the_estimates(
    security, tmp_path, runtime
):
    study = make_study(tmp_path)
    sup = nca_supervisor(tmp_path, runtime)
    with nca_client(security, tmp_path, study, runtime=runtime, supervisor=sup) as api:
        runs = {}
        for name, steps in (
            ("default", resolving_steps()),
            ("ug/L", [*resolving_steps(), CONVERT]),
        ):
            run = start_run(api, ready_recipe(api, steps=steps), "PARENT")
            folder = study / ".opncafyr" / "runs" / run["run_id"]
            preset = json.loads((folder / "input" / "opnca-mapping.json").read_text("utf-8"))
            assert preset["units"] == run["analysis_units"]
            out = review_and_replay(api, study, run["run_id"])
            assert out["run"]["replay"]["status"] == "passed", out["run"]["replay"]
            effective = out["run"]["nca_settings"]["effective_settings"]
            assert effective["units"] == run["analysis_units"]
            assert effective["units_source"] == "preset"
            runs[name] = (run, folder)

    run, folder = runs["ug/L"]
    assert runs["default"][0]["analysis_units"] == DEFAULT
    assert run["analysis_units"] == {**DEFAULT, "concentration": "ug/L"}
    script = (folder / "analysis.R").read_text("utf-8")
    assert 'opnca_units(concentration = "ug/L", time = "h", dose = "mg")' in script

    before = _long(study, runs["default"][0]["run_id"])
    after = _long(study, run["run_id"])
    assert before.keys() == after.keys()
    relabelled = 0
    for key, row in before.items():
        assert after[key]["value"] == row["value"], key
        assert after[key]["unit"] == row["unit"].replace("ng/mL", "ug/L"), key
        relabelled += "ng/mL" in row["unit"]
    assert relabelled  # Cmax, AUC, clearance and volume all carry the concentration unit
    metrics = {k[1]: r["unit"] for k, r in after.items()}
    assert metrics["cmax"] == "ug/L" and "ug/L" in metrics["auclast"]
