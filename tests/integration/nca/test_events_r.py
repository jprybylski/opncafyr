"""Event records through the pinned R and, end to end, opnca-r (#58).

The partitions, doses and times expected here come from ``fixtures/events.expected.json``
(``tests/fixtures/make_event_fixtures.py``).
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import shutil
import subprocess

import pytest

from event_helpers import (
    EVENT_FILES,
    EVENTS_EXPECTED,
    GENERIC,
    MAIN,
    PLANTED,
    SETUP,
    event_recipe,
    planted_case,
    put_events,
    standard_events,
)
from nca_helpers import (
    FIXTURES,
    apply,
    make_study,
    nca_client,
    nca_supervisor,
    new_recipe,
    review_and_replay,
    start_run,
)
from opncafyr.core import runs as R
from opncafyr.core.manifest import validate

pytestmark = pytest.mark.integration

LINES = EVENTS_EXPECTED["files"][MAIN]["lines"]


@pytest.fixture
def study(tmp_path):
    return make_study(tmp_path, EVENT_FILES)


@pytest.fixture
def api(security, tmp_path, study, runtime):
    with nca_client(security, tmp_path, study, runtime=runtime) as client:
        yield client


def _folder(study, recipe_id):
    folders = sorted((study / ".opncafyr" / "cleaned").glob(f"{recipe_id}-*"))
    assert len(folders) == 1, folders
    return folders[0]


def _csv(path):
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _source(name):
    with (FIXTURES / name).open(newline="", encoding="utf-8") as fh:
        return {str(i): row for i, row in enumerate(csv.DictReader(fh), start=2)}


def _checks(api, rid, analyte):
    r = api.post(f"/api/nca/recipes/{rid}/checks", json={"analyte_value": analyte})
    assert r.status_code == 200, r.text
    return r.json()["findings"]


def _blocking(findings):
    return {f["check"]: f for f in findings if f["level"] == "blocking"}


# --- The standard setup -------------------------------------------------------------


def test_every_record_lands_in_its_expected_partition(api, study):
    rid, result = event_recipe(api)
    folder = _folder(study, rid)
    assert result["events"]["partitions"] == {
        **dict.fromkeys(
            (
                "selected",
                "predose_set_aside",
                "outside_interval",
                "dose",
                "other_endpoint",
                "other_record",
                "rejected",
                "unresolved",
            ),
            0,
        ),
        **EVENTS_EXPECTED["files"][MAIN]["partitions"],
    }
    records = {r["line"]: r for r in _csv(folder / "records.csv")}
    assert set(records) == set(LINES)
    for line, want in LINES.items():
        assert records[line]["partition"] == want["partition"], line
        if want["partition"] == "selected":
            assert records[line]["dose_lines"] == want["dose_lines"], line
    assert {r["category"] for r in records.values() if r["partition"] == "dose"} == {"used"}


def test_prepared_rows_are_the_pk_observations_with_their_dose(api, study):
    rid, _ = event_recipe(api)
    source = _source(MAIN)
    prepared = _csv(_folder(study, rid) / "prepared.csv")
    selected = [line for line, e in LINES.items() if e["partition"] == "selected"]
    assert [r["opncafyr_line"] for r in prepared] == selected
    for row in prepared:
        want = LINES[row["opncafyr_line"]]
        raw = source[row["opncafyr_line"]]
        # Raw columns are kept exactly; BLQ and MDV rows are not changed or dropped.
        assert {k: row[k] for k in raw} == raw
        assert row["CMT"] == want["endpoint"] and row["EVID"] == "0"
        assert row["opncafyr_time_after_dose"] == want["time_after_dose"]
        assert row["opncafyr_dose_lines"] == want["dose_lines"]
        dose = source[want["dose_lines"]]
        assert row["opncafyr_dose"] == dose["AMT"] == "100"
        assert row["opncafyr_dose_unit"] == dose["EVENTU"] == "mg"
        assert row["opncafyr_dose_time"] == dose["TIME"]
        assert row["opncafyr_route"] == "extravascular"
        assert row["opncafyr_dose_number"] == "1"
    assert not any(r["DV"] == "0" for r in prepared)  # no zero sample made up


def test_the_source_is_unchanged_and_the_script_runs_standalone(api, study, runtime, tmp_path):
    before = (study / "source-data" / MAIN).read_bytes()
    rid, _ = event_recipe(api)
    folder = _folder(study, rid)
    assert (study / "source-data" / MAIN).read_bytes() == before
    assert (
        hashlib.sha256(before).hexdigest()
        == hashlib.sha256((FIXTURES / MAIN).read_bytes()).hexdigest()
    )
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
    for name in (
        "cleaned.csv",
        "rejected.csv",
        "steps.json",
        "prepared.csv",
        "records.csv",
        "events.json",
    ):
        assert (script.parent / "rerun" / name).read_bytes() == (folder / name).read_bytes()


def test_endpoints_stay_distinct_and_match_exactly(api):
    rid, _ = event_recipe(api)
    out = api.get(f"/api/nca/recipes/{rid}/analytes").json()
    # Only PK endpoints reach the analyte list: not the dose code 1, not the PD code 4.
    assert [v["value"] for v in out["values"]] == ["2", "3"]
    assert not _blocking(_checks(api, rid, "2"))
    assert "analyte_no_rows" in _blocking(_checks(api, rid, "4"))
    assert "analyte_no_rows" in _blocking(_checks(api, rid, "2.0"))


def test_mapping_help_suggests_the_prepared_columns(api):
    rid, _ = event_recipe(api, mapping={})
    help_ = api.get(f"/api/nca/recipes/{rid}/mapping-help").json()
    picked = {s["role"]: s["column"] for s in help_["suggestions"]}
    assert picked["ACTUAL_TIME"] == "opncafyr_time_after_dose"
    assert picked["DOSE"] == "opncafyr_dose" and picked["ROUTE"] == "opncafyr_route"
    assert help_["mapping"] == {"roles": {}}  # suggested, never applied


def test_raw_time_or_amt_mapped_blocks(api):
    mapping = json.loads(json.dumps(SETUP["mapping"]))
    mapping["roles"]["ACTUAL_TIME"] = "TIME"
    mapping["roles"]["DOSE"] = "AMT"
    rid, _ = event_recipe(api, mapping=mapping)
    found = [f for f in _checks(api, rid, "2") if f["check"] == "event_mapping_mismatch"]
    assert {f["role"] for f in found} == {"ACTUAL_TIME", "DOSE"}


def test_nominal_time_as_the_event_time_blocks(api):
    events = standard_events(predose="keep")
    events["columns"]["time"] = "NOMTIME"
    rid, _ = event_recipe(api, events=events)
    assert "time_nominal_as_actual" in _blocking(_checks(api, rid, "2"))


def test_eventu_is_never_one_unit_for_every_record(api):
    mapping = json.loads(json.dumps(SETUP["mapping"]))
    mapping["units"]["DOSE"] = {"column": "EVENTU"}  # on observations it holds ng/mL
    rid, _ = event_recipe(api, mapping=mapping)
    blocking = _blocking(_checks(api, rid, "2"))
    assert "event_mapping_mismatch" in blocking and "units_undeclared" in blocking


def test_a_changed_choice_makes_a_new_result(api, study):
    rid, _ = event_recipe(api)
    first = _folder(study, rid).name
    put_events(api, rid, standard_events(predose="set_aside"))
    out = apply(api, rid)
    assert out["events"]["partitions"]["predose_set_aside"] == 12
    folders = {p.name for p in (study / ".opncafyr" / "cleaned").glob(f"{rid}-*")}
    assert first in folders and len(folders) == 2


# --- Planted cases ---------------------------------------------------------------------


def _planted(api, study, **choices):
    rid, result = event_recipe(
        api,
        PLANTED,
        events=standard_events(**choices) if choices else {**standard_events(), "choices": {}},
    )
    groups = {g["check"]: g["lines"] for g in result["events"]["unresolved"]}
    return rid, result, groups


def test_each_planted_case_blocks_until_settled(api, study):
    rid, _, groups = _planted(api, study)
    for case in EVENTS_EXPECTED["files"][PLANTED]["cases"]:
        if case.get("found_by") == "checks":
            continue
        assert set(case["lines"]) <= set(groups.get(case["check"], [])), case["id"]
        if "observations" in case:
            assert set(case["observations"]) <= set(groups[case["observation_check"]])
    blocking = _blocking(_checks(api, rid, "2"))
    for case in EVENTS_EXPECTED["files"][PLANTED]["cases"]:
        assert case["check"] in blocking, case["id"]
    assert blocking["event_multiple_doses"]["choice"] == "dose_interval"


def test_choices_settle_their_cases(api, study):
    rid, result, groups = _planted(
        api,
        study,
        dose_interval="last",
        same_time_doses="sum",
        predose="set_aside",
        observation_at_dose_time="after",
        unmatched="reject",
    )
    folder = _folder(study, rid)
    prepared = {r["opncafyr_line"]: r for r in _csv(folder / "prepared.csv")}
    multi = planted_case("multiple_doses")
    # Last dose (24 h): the sample at 24 h is "after" it, so time after dose 0.
    assert [
        prepared[str(n)]["opncafyr_time_after_dose"]
        for n in (multi["at_dose_time"], *multi["last"])
    ] == ["0", "1", "4"]
    assert all(str(n) not in prepared for n in multi["first"])
    split = planted_case("same_time_doses")
    for n in split["observations"]:
        assert prepared[str(n)]["opncafyr_dose"] == split["summed"]
        assert prepared[str(n)]["opncafyr_dose_lines"] == ";".join(map(str, split["lines"]))
    rejected = _csv(folder / "rejected.csv")
    assert [int(r["line"]) for r in rejected] == planted_case("unmatched")["lines"]
    assert all(r["reason"].startswith("No dose record") for r in rejected)
    assert str(planted_case("predose")["lines"][0]) not in prepared
    for settled in (
        "event_multiple_doses",
        "event_same_time_doses",
        "event_unmatched",
        "event_predose",
        "event_at_dose_time",
    ):
        assert settled not in groups
    assert result["events"]["partitions"]["predose_set_aside"] == 1


def test_first_interval_and_before(api, study):
    rid, _, groups = _planted(
        api, study, dose_interval="first", observation_at_dose_time="before", predose="keep"
    )
    prepared = {r["opncafyr_line"]: r for r in _csv(_folder(study, rid) / "prepared.csv")}
    multi = planted_case("multiple_doses")
    times = [
        prepared[str(n)]["opncafyr_time_after_dose"]
        for n in (*multi["first"], multi["at_dose_time"])
    ]
    assert times == ["1", "4", "24"]
    assert prepared[str(planted_case("predose")["lines"][0])]["opncafyr_time_after_dose"] == "-0.5"


def test_an_interval_rule_alone_asks_about_samples_at_a_dose_time(api, study):
    _, _, groups = _planted(api, study, dose_interval="first", predose="keep")
    assert planted_case("multiple_doses")["at_dose_time"] in groups["event_at_dose_time"]


def test_split_doses_with_different_units_are_never_summed(api, study):
    _, _, groups = _planted(api, study, same_time_doses="sum")
    case = planted_case("same_time_doses_differ")
    assert set(case["lines"]) <= set(groups[case["with_sum"]])


def test_a_dose_number_past_the_last_is_unmatched(api, study):
    _, _, groups = _planted(api, study, dose_interval="number", dose_number=3)
    multi = planted_case("multiple_doses")
    assert set(multi["first"]) <= set(groups["event_unmatched"])


def test_an_excluded_dose_record_blocks(api, study):
    steps = [
        *SETUP["steps"],
        {
            "op": "exclude",
            "args": {"where": {"column": "opncafyr_line", "equals": "2"}, "reason": "test"},
        },
    ]
    rid, result = event_recipe(
        api, PLANTED, steps=steps, events={**standard_events(), "choices": {}}
    )
    groups = {g["check"]: g["lines"] for g in result["events"]["unresolved"]}
    assert groups["event_excluded_dose"] == [2]


# --- A loosely structured single-analyte file -------------------------------------------


def test_a_generic_file_needs_explicit_declarations(api, study):
    spec = EVENTS_EXPECTED["files"][GENERIC]
    doc = new_recipe(api, GENERIC, [spec["steps"][0]])  # clean_names only
    apply(api, doc["id"])
    help_ = api.get(f"/api/nca/recipes/{doc['id']}/events-help").json()
    assert help_["suggestion"]["layout"] == "observation_rows"
    assert help_["suggestion"]["needs_analyte"] is True
    r = api.post(f"/api/nca/recipes/{doc['id']}/checks", json={"analyte_value": "DRUGX"})
    assert r.status_code == 200
    # No analyte, route or unit is inferred from the file's name or headers.
    mapping_help = api.get(f"/api/nca/recipes/{doc['id']}/mapping-help").json()
    assert {p["role"] for p in mapping_help["problems"] if p["check"] == "role_unmapped"} >= {
        "ANALYTE",
        "ROUTE",
    }


# --- End to end through opnca-r ----------------------------------------------------------


@pytest.fixture
def full(security, tmp_path, runtime):
    study = make_study(tmp_path, EVENT_FILES)
    sup = nca_supervisor(tmp_path, runtime)
    with nca_client(security, tmp_path, study, runtime=runtime, supervisor=sup) as api:
        yield api, study


def test_an_event_record_run_completes_and_replays(full):
    api, study = full
    rid, _ = event_recipe(api)
    run = start_run(api, rid, "2", specimen=None)
    folder = study / ".opncafyr" / "runs" / run["run_id"]
    settings = json.loads((folder / "settings.json").read_text("utf-8"))
    validate(settings, "run-settings-v1")
    assert settings["schema_version"] == "1.1"
    prep = settings["preparation"]
    assert prep["rules"] == SETUP["events"] and prep["accounting"]["selected"] == 96
    assert run["lineage"]["events_sha256"] == prep["events_sha256"]
    records = folder / prep["records"]["path"]
    assert hashlib.sha256(records.read_bytes()).hexdigest() == prep["records"]["sha256"]
    frozen = _csv(folder / "input" / "analysis.csv")
    want = [n for n, e in LINES.items() if e["partition"] == "selected" and e["endpoint"] == "2"]
    assert [r["opncafyr_line"] for r in frozen] == want
    assert {r["opncafyr_dose"] for r in frozen} == {"100"}
    assert settings["analysis_units"] == {"concentration": "ng/mL", "time": "h", "dose": "mg"}

    out = review_and_replay(api, study, run["run_id"])
    assert out["run"]["replay"]["status"] == "passed"
    done = api.post(f"/api/nca/runs/{run['run_id']}/complete")
    assert done.status_code == 200, done.text
    assert R.verify_run(folder)["ok"] is True

    other = start_run(api, rid, "3", specimen=None)
    assert other["run_id"] != run["run_id"]
    assert other["analysis_input"]["rows"] == 48


def test_a_generic_file_runs_after_declaring_analyte_route_and_units(full):
    api, study = full
    spec = EVENTS_EXPECTED["files"][GENERIC]
    missing = spec["cases"][0]["lines"]
    steps = [
        *spec["steps"],
        {
            "op": "filter_rows",
            "args": {
                "where": {"column": "opncafyr_line", "in": [str(n) for n in missing]},
                "reason": "Sample not analysed.",
            },
        },
    ]
    doc = new_recipe(api, GENERIC, steps)
    apply(api, doc["id"])
    r = api.put(f"/api/nca/recipes/{doc['id']}/mapping", json={"mapping": spec["mapping"]})
    assert r.status_code == 200, r.text
    run = start_run(api, doc["id"], "DRUGX", specimen=None)
    out = review_and_replay(api, study, run["run_id"])
    assert out["run"]["replay"]["status"] == "passed"
    assert run["schema_version"] == "1.0"
