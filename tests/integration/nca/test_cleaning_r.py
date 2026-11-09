"""Cleaning recipes run by the pinned R (#22)."""

from __future__ import annotations

import csv
import hashlib
import json
import os
import shutil
import subprocess

import pytest

from nca_helpers import (
    EXPECTED,
    FIXTURES,
    SOURCE,
    apply,
    make_study,
    nca_client,
    new_recipe,
    wait_job,
)
from opncafyr.workflows.nca.clean_compile import r_string

pytestmark = pytest.mark.integration

FINDINGS = EXPECTED["findings"][SOURCE]
SCENARIOS = {s["id"]: s for s in EXPECTED["scenarios"][SOURCE]}


@pytest.fixture
def study(tmp_path):
    return make_study(tmp_path)


@pytest.fixture
def api(security, tmp_path, study, runtime):
    with nca_client(security, tmp_path, study, runtime=runtime) as client:
        yield client


def _folder(study, result_output):
    return study / result_output


def _lines(check, **match):
    out = []
    for f in FINDINGS:
        if f["check"] == check and all(f.get(k) == v for k, v in match.items()):
            out.append(f["lines"])
    return out


EVERY_STEP = [
    {"op": "trim", "args": {"columns": ["PCTESTCD"]}},
    {"op": "parse_type", "args": {"column": "CONC", "type": "number", "decimal_mark": ","}},
    {"op": "recode", "args": {"column": "ROUTE", "map": {"EXTRAVASULAR": "extravascular"}}},
    {
        "op": "derive",
        "args": {"target": "KEY", "fn": "concat", "from": ["USUBJID", "AFRLT"], "sep": "|"},
    },
    {"op": "derive", "args": {"target": "SITE", "fn": "constant", "value": "A"}},
    {"op": "derive", "args": {"target": "T2", "fn": "copy", "from": "AFRLT"}},
    {
        "op": "derive_elapsed_time",
        "args": {
            "target": "ELTM",
            "from": "PCDTC",
            "reference": "EXSTDTC",
            "unit": "h",
            "time_zone": "all local",
        },
    },
    {
        "op": "convert_units",
        "args": {"value_column": "CONC", "unit_column": "PCSTRESU", "from": "ng/mL", "to": "ug/L"},
    },
    {"op": "flag", "args": {"target": "BLQ_L", "column": "BLQFL", "table": {"Y": True, "": False}}},
    {
        "op": "exclude",
        "args": {"where": {"column": "USUBJID", "equals": "TA-008"}, "reason": "no dose record"},
    },
    {
        "op": "filter_rows",
        "args": {
            "where": {"column": "opncafyr_line", "in": ["77", "120"]},
            "reason": "listed for removal",
        },
    },
    {"op": "report_dupes", "args": {"columns": ["USUBJID", "PCTESTCD", "AFRLT"]}},
    {"op": "rename", "args": {"from": "PCSPEC", "to": "SPECIMEN"}},
    {"op": "clean_names", "args": {}},
]


def test_every_step_accounts_for_every_row(api, study):
    doc = new_recipe(api, steps=EVERY_STEP)
    out = apply(api, doc["id"])
    summary = out["summary"]
    rows = summary["source_rows"]
    assert rows == EXPECTED["files"][SOURCE]["rows"]
    for step in summary["steps"]:
        assert step["rows_in"] == rows
        assert step["rows_in"] == step["rows_out"] + step["rows_rejected"]
        rows = step["rows_out"]
    assert summary["rows_out"] + summary["rows_rejected"] == summary["source_rows"]
    assert {(int(r["line"]), int(r["step"])) for r in out["rejected"]} == {(77, 11), (120, 11)}
    assert all(r["reason"] == "listed for removal" for r in out["rejected"])
    # Exclusion marks rows; it never removes them.
    excl = next(s for s in summary["steps"] if s["op"] == "exclude")
    assert excl["rows_rejected"] == 0 and excl["cells_changed"] > 0


def test_duplicates_are_reported_not_removed(api):
    doc = new_recipe(
        api, steps=[{"op": "report_dupes", "args": {"columns": ["USUBJID", "PCTESTCD", "AFRLT"]}}]
    )
    out = apply(api, doc["id"])
    step = out["summary"]["steps"][0]
    assert step["rows_out"] == step["rows_in"] and step["rows_rejected"] == 0
    groups = [sorted(g) for g in step["dupes"]]
    for lines in _lines("duplicate_key"):
        assert sorted(lines) in groups


def test_source_bytes_are_never_changed(api, study):
    path = study / "source-data" / SOURCE
    before = (path.read_bytes(), path.stat().st_mtime_ns)
    doc = new_recipe(api, steps=EVERY_STEP)
    apply(api, doc["id"], "preview")
    apply(api, doc["id"])
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before
    fixture = (FIXTURES / SOURCE).read_bytes()
    assert hashlib.sha256(before[0]).hexdigest() == hashlib.sha256(fixture).hexdigest()


def test_generated_script_runs_standalone(api, study, runtime, tmp_path):
    doc = new_recipe(api, steps=EVERY_STEP)
    apply(api, doc["id"])
    folder = next((study / ".opncafyr" / "cleaned").glob(f"{doc['id']}-*"))
    script = tmp_path / "copy" / "clean.R"
    script.parent.mkdir()
    shutil.copyfile(folder / "clean.R", script)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("CLEAN_", "OPNCAFYR_"))}
    env["R_LIBS"] = str(runtime.r_library)
    subprocess.run(
        [str(runtime.executable("rscript")), "--vanilla", str(script)],
        cwd=study,
        env=env,
        check=True,
        capture_output=True,
    )
    for name in ("cleaned.csv", "rejected.csv", "steps.json"):
        assert (script.parent / "rerun" / name).read_bytes() == (folder / name).read_bytes()


def test_apply_reuses_a_valid_result(api):
    doc = new_recipe(api, steps=EVERY_STEP[:2])
    apply(api, doc["id"])
    again = api.post(f"/api/nca/recipes/{doc['id']}/apply").json()
    assert again["cached"] is True and again["job_id"] is None


def test_preview_runs_the_same_script_on_the_first_rows(api):
    doc = new_recipe(api, steps=EVERY_STEP)
    out = apply(api, doc["id"], "preview")
    assert out["summary"]["sample_rows"] == 200
    assert len(out["summary"]["steps"]) == len(EVERY_STEP)


def test_derive_elapsed_time_scenario(api):
    scenario = SCENARIOS["derive_elapsed_time"]
    doc = new_recipe(api, steps=scenario["change"]["recipe"])
    out = apply(api, doc["id"])
    step = out["summary"]["steps"][0]
    failed = {
        n for i in step["issues"] if i["kind"] == "time_derivation_failed" for n in i["lines"]
    }
    expected = {n for inc in scenario["includes"] for n in inc["lines"]}
    assert failed == expected
    folder = next(p for p in (api.app.state.workspace.root / ".opncafyr" / "cleaned").iterdir())
    with (folder / "cleaned.csv").open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if int(row["opncafyr_line"]) in expected:
                assert row["ELTM"] == ""
            elif row["AFRLT"] not in ("", "~8"):
                assert abs(float(row["ELTM"]) - float(row["AFRLT"])) < 5e-5, row["opncafyr_line"]


def test_trim_scenario_makes_the_variant_a_metab_row(api):
    doc = new_recipe(api, steps=SCENARIOS["analyte_code_trimmed"]["change"]["recipe"])
    out = apply(api, doc["id"])
    assert out["summary"]["steps"][0]["cells_changed"] == 1
    folder = next(p for p in (api.app.state.workspace.root / ".opncafyr" / "cleaned").iterdir())
    with (folder / "cleaned.csv").open(newline="", encoding="utf-8") as fh:
        codes = {row["PCTESTCD"] for row in csv.DictReader(fh)}
    assert codes == {"PARENT", "METAB"}


def test_a_ragged_file_stops_with_its_lines(security, tmp_path, runtime):
    study = make_study(tmp_path, files=())
    (study / "source-data" / "bad.csv").write_text("A,B\n1,2\n3\n4,5\n", "utf-8")
    with nca_client(security, tmp_path, study, runtime=runtime) as api:
        doc = new_recipe(api, name="bad.csv")
        job = wait_job(api, api.post(f"/api/nca/recipes/{doc['id']}/apply").json()["job_id"])
        assert job["state"] == "failed"
        assert "Lines 3 do not have as many fields" in job["message"]


@pytest.mark.parametrize(
    "text",
    ['a"); system("touch pwned"); x <- ("', "back\\slash", "new\nline\r\t", "µg/mL — ü 😀"],
)
def test_r_string_round_trips_through_r(runtime, tmp_path, text):
    script = tmp_path / "t.R"
    out = tmp_path / "out.txt"
    script.write_text(
        f"writeLines({r_string(text)}, {r_string(str(out))}, useBytes = TRUE, sep = '')\n",
        "utf-8",
    )
    subprocess.run(
        [str(runtime.executable("rscript")), "--vanilla", str(script)],
        check=True,
        capture_output=True,
        cwd=tmp_path,
    )
    assert out.read_bytes() == text.encode("utf-8")
    assert not (tmp_path / "pwned").exists()


def test_steps_json_is_valid_json(api, study):
    doc = new_recipe(api, steps=EVERY_STEP[:1])
    apply(api, doc["id"])
    folder = next((study / ".opncafyr" / "cleaned").glob(f"{doc['id']}-*"))
    meta = json.loads((folder / "meta.json").read_text("utf-8"))
    assert meta["recipe_id"] == doc["id"] and meta["sample_rows"] == 0


def test_editing_a_step_leaves_the_earlier_result_stale_and_the_source_alone(api, study):
    # #63: Edit saves one step in place. The applied result belongs to the old recipe hash,
    # so it is no longer offered; applying again gives the new one. The source is untouched.
    source = study / "source-data" / SOURCE
    source_bytes = source.read_bytes()
    doc = new_recipe(
        api,
        steps=[
            {"op": "trim", "args": {"columns": ["PCTESTCD"]}, "note": "ids"},
            {
                "op": "exclude",
                "args": {"where": {"column": "PCSPEC", "equals": "URINE"}, "reason": "Not plasma"},
            },
        ],
    )
    first = apply(api, doc["id"])
    assert api.get(f"/api/nca/recipes/{doc['id']}/result?kind=full").status_code == 200
    before = api.get(f"/api/nca/recipes/{doc['id']}").json()
    edited = before["recipe"]
    edited["steps"][1]["args"]["reason"] = "Urine is not part of this analysis"
    r = api.put(f"/api/nca/recipes/{doc['id']}", json={"recipe": edited})
    assert r.status_code == 200, r.text
    assert r.json()["sha256"] != before["sha256"]
    stale = api.get(f"/api/nca/recipes/{doc['id']}/result?kind=full")
    assert stale.status_code == 404 and stale.json()["detail"]["code"] == "not_ready"
    assert api.get(f"/api/nca/recipes/{doc['id']}/analytes").status_code == 409
    second = apply(api, doc["id"])
    assert second["summary"]["steps"][1]["op"] == "exclude"
    assert first["summary"]["source_rows"] == second["summary"]["source_rows"]
    assert source.read_bytes() == source_bytes
