"""Event records without R (#58): schema, rules, hashing, script, reconciliation, layout
suggestion, findings, mapping rules, snapshot schemas. The R stage itself is tested in
tests/integration/nca/test_events_r.py."""

from __future__ import annotations

import copy
import csv
import hashlib
import io
import json
import re
from pathlib import Path

import pytest

from event_helpers import EVENTS_EXPECTED, GENERIC, MAIN, PLANTED, SETUP, standard_events
from nca_helpers import FIXTURES, make_study, nca_client, new_recipe
from opncafyr.core.manifest import load_schema, validate
from opncafyr.workflows.nca import checks as C
from opncafyr.workflows.nca import events as E
from opncafyr.workflows.nca import mapping as M
from opncafyr.workflows.nca import recipe as R
from opncafyr.workflows.nca.clean_compile import compile_recipe
from opncafyr.workflows.nca.cleaning import PARTITIONS, check_output, plan

ROOT = Path(__file__).resolve().parents[1]


def _header(name: str = MAIN) -> list[str]:
    with (FIXTURES / name).open(newline="", encoding="utf-8") as fh:
        return next(csv.reader(fh))


def _table(name: str = MAIN) -> tuple[list[str], list[list[str]]]:
    with (FIXTURES / name).open(newline="", encoding="utf-8") as fh:
        rows = list(csv.reader(fh))
    return ["opncafyr_line", *rows[0]], [[str(i), *r] for i, r in enumerate(rows[1:], start=2)]


def _doc(events=None, steps=None) -> dict:
    base = R.new_recipe("ev", "src")
    base["steps"] = copy.deepcopy(SETUP["steps"] if steps is None else steps)
    return R.set_events(base, standard_events() if events is None else events)


# --- Schema and versioning -------------------------------------------------------------


def test_events_need_version_1_1_and_1_1_needs_events():
    doc = _doc()
    R.validate_schema(doc)
    assert doc["schema_version"] == "1.1"
    with pytest.raises(R.RecipeError):
        R.validate_schema({**doc, "schema_version": "1.0"})
    with pytest.raises(R.RecipeError):
        R.validate_schema({k: v for k, v in doc.items() if k != "events"})


@pytest.mark.parametrize(
    "choices",
    [
        {"dose_interval": "number"},
        {"dose_number": 2},
        {"dose_interval": "first", "dose_number": 2},
        {"dose_interval": "middle"},
        {"same_time_doses": "first_listed"},
        {"unmatched": "exclude"},
        {"predose": "zero"},
    ],
)
def test_choices_are_a_fixed_vocabulary(choices):
    with pytest.raises(R.RecipeError):
        R.validate_schema(_doc(standard_events(**choices)))


def test_record_kinds_and_endpoints_are_a_fixed_vocabulary():
    events = standard_events()
    events["record_kinds"]["3"] = "reset"
    with pytest.raises(R.RecipeError):
        R.validate_schema(_doc(events))
    events = standard_events()
    events["endpoints"]["4"] = "pd"
    with pytest.raises(R.RecipeError):
        R.validate_schema(_doc(events))


def test_removing_the_block_restores_the_old_recipe_and_hash():
    old = R.new_recipe("ev", "src")
    assert R.recipe_hash(old) == (
        hashlib.sha256(
            json.dumps(old, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
    )
    with_events = R.set_events(old, standard_events())
    assert R.steps_hash(with_events) != R.steps_hash(old)
    back = R.set_events(with_events, None)
    assert back == old and R.recipe_hash(back) == R.recipe_hash(old)


def test_a_1_0_recipe_hash_is_unchanged():
    # Pinned: the canonical form of a recipe without events must never move (#58 keeps
    # every earlier recipe's hash, acknowledgement keys and cached result).
    doc = {"schema_version": "1.0", "id": "two-analyte-csv-1a2b", "source_id": "s", "steps": []}
    assert R.steps_hash(doc) == "59f6bef7279871f2d44ed3901fa2efd0ca8f8592ee214f07be1beb376995d536"
    assert R.events_hash(doc) is None


def test_a_changed_choice_changes_every_hash():
    a = _doc(standard_events(predose="keep"))
    b = _doc(standard_events(predose="set_aside"))
    assert R.steps_hash(a) != R.steps_hash(b)
    assert R.events_hash(a) != R.events_hash(b)
    assert R.recipe_hash(a) != R.recipe_hash(b)


# --- Rules -----------------------------------------------------------------------------


def test_the_standard_block_checks_and_adds_the_prepared_columns():
    plan_ = R.check(_doc(), _header())
    assert plan_.columns[-1] == "route"
    assert plan_.analysis_columns == [*plan_.columns, *R.EVENT_COLUMNS]
    lin = plan_.lineage[R.TIME_AFTER_DOSE_COLUMN]
    assert lin.origin == "TIME" and lin.ops[-1] == "time_after_dose"


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda e: e["columns"].update(time="NOPE"), "no column NOPE"),
        (lambda e: e["columns"].update(dv="TIME"), "both"),
        (lambda e: e.update(record_kinds={"0": "observation"}), "dose"),
        (lambda e: e.update(endpoints={"2": "other"}), "PK analyte"),
        (lambda e: e.pop("mdv"), "MDV"),
        (lambda e: e["columns"].update(subject="opncafyr_line"), "managed"),
    ],
)
def test_events_rules(change, message):
    events = standard_events()
    change(events)
    with pytest.raises(R.RecipeError, match=message):
        R.check(_doc(events), _header())


@pytest.mark.parametrize("name", R.EVENT_COLUMNS)
def test_prepared_column_names_are_reserved(name):
    with pytest.raises(R.RecipeError, match="reserved"):
        R.plan_columns(R.new_recipe("ev", "src"), [*_header(), name])
    steps = [{"op": "derive", "args": {"target": name, "fn": "constant", "value": "1"}}]
    with pytest.raises(R.RecipeError, match="event stage"):
        R.plan_columns({**R.new_recipe("ev", "src"), "steps": steps}, _header())
    steps = [{"op": "rename", "args": {"from": "DV", "to": name}}]
    with pytest.raises(R.RecipeError, match="managed"):
        R.plan_columns({**R.new_recipe("ev", "src"), "steps": steps}, _header())


def test_clean_names_can_not_make_a_prepared_column():
    steps = [{"op": "clean_names", "args": {}}]
    with pytest.raises(R.RecipeError, match="reserved"):
        R.plan_columns({**R.new_recipe("ev", "src"), "steps": steps}, ["OPNCAFYR DOSE", "ID"])


def test_the_mapping_may_name_prepared_columns_only_with_events():
    doc = _doc()
    doc["mapping"] = SETUP["mapping"]
    R.check(doc, _header())
    plain = R.set_events(doc, None)
    with pytest.raises(R.RecipeError, match="opncafyr_"):
        R.check(plain, _header())


# --- The generated script --------------------------------------------------------------


def test_a_recipe_without_events_compiles_exactly_as_before():
    doc = R.new_recipe("ev", "src")
    script = compile_recipe(doc, source_rel="source-data/x.csv", delimiter=",")
    assert "oc_prepare_events" not in script and "Event records" not in script
    assert "\noc_run(input, output" in script


def test_event_values_reach_r_only_as_string_literals():
    events = standard_events()
    events["endpoints"] = {'2"); system("touch pwned"); ("': "pk", "3": "pk"}
    script = compile_recipe(_doc(events), source_rel="source-data/x.csv", delimiter=",")
    tail = script.split("events <- list(", 1)[1]
    assert 'system("touch' not in tail
    assert '\\"); system(\\"touch pwned\\"); (\\"' in tail
    assert "oc_prepare_events(df, output, events" in tail
    assert R.events_hash(_doc(events)) in tail


def test_the_dose_number_is_an_integer_literal():
    script = compile_recipe(
        _doc(standard_events(dose_interval="number", dose_number=2)),
        source_rel="x.csv",
        delimiter=",",
    )
    assert re.search(r'choices = list\(dose_interval = "number", dose_number = 2L\)', script)


# --- Reconciliation of a result folder -------------------------------------------------


def _target(tmp_path):
    study = make_study(tmp_path, (MAIN,))
    data = (study / "source-data" / MAIN).read_bytes()
    source = {
        "id": "s",
        "path": f"source-data/{MAIN}",
        "sha256": hashlib.sha256(data).hexdigest(),
        "size_bytes": len(data),
    }
    doc = _doc()
    doc["source_id"] = "s"
    return plan(study, {"sources": [source]}, doc), study


def _write_csv(path, header, rows):
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(header)
    w.writerows(rows)
    path.write_text(buf.getvalue(), "utf-8")


def _result(target, folder):
    """A consistent result folder for the standard block, made without R."""
    header, rows = _table()
    lines = EVENTS_EXPECTED["files"][MAIN]["lines"]
    folder.mkdir(parents=True)
    (folder / "clean.R").write_text(target.script, "utf-8")
    (folder / "meta.json").write_text(json.dumps(target.meta()), "utf-8")
    _write_csv(folder / "cleaned.csv", header, rows)
    _write_csv(folder / "rejected.csv", ["line", "step", "reason"], [])
    steps = {
        "steps_sha256": target.steps_sha256,
        "source_rows": len(rows),
        "rows_out": len(rows),
        "rows_rejected": 0,
        "steps": [{"index": 1, "rows_in": len(rows), "rows_out": len(rows), "rows_rejected": 0}],
    }
    (folder / "steps.json").write_text(json.dumps(steps), "utf-8")
    selected = [r for r in rows if lines[r[0]]["partition"] == "selected"]
    _write_csv(folder / "prepared.csv", header, selected)
    _write_csv(
        folder / "records.csv",
        ["line", "partition", "category", "dose_lines"],
        [[r[0], lines[r[0]]["partition"], "", ""] for r in rows],
    )
    counts = dict.fromkeys(PARTITIONS, 0)
    counts.update(EVENTS_EXPECTED["files"][MAIN]["partitions"])
    report = {
        "events_sha256": target.events_sha256,
        "rows_in": len(rows),
        "partitions": counts,
        "doses_used": 6,
        "doses_unused": 0,
        "rows_rejected": 0,
        "unresolved": [],
    }
    (folder / "events.json").write_text(json.dumps(report), "utf-8")
    return report


def test_a_consistent_event_result_passes(tmp_path):
    target, study = _target(tmp_path)
    _result(target, study / "out")
    assert check_output(study / "out", target) is None


@pytest.mark.parametrize(
    ("damage", "message"),
    [
        (lambda f, r: r.update(events_sha256="0" * 64), "different events block"),
        (lambda f, r: r["partitions"].update(dose=5), "don't add up"),
        (lambda f, r: r["partitions"].pop("rejected"), "every partition"),
        (lambda f, r: (f / "prepared.csv").write_text("opncafyr_line\n2\n", "utf-8"), "prepared"),
        (lambda f, r: (f / "records.csv").unlink(), "missing"),
        (lambda f, r: r.update(rows_rejected=2), "rejected"),
    ],
)
def test_a_broken_event_result_fails(tmp_path, damage, message):
    target, study = _target(tmp_path)
    folder = study / "out"
    report = _result(target, folder)
    damage(folder, report)
    if (folder / "events.json").exists():
        (folder / "events.json").write_text(json.dumps(report), "utf-8")
    problem = check_output(folder, target)
    assert problem and message in problem


def test_records_must_list_every_line_once(tmp_path):
    target, study = _target(tmp_path)
    folder = study / "out"
    _result(target, folder)
    text = (folder / "records.csv").read_text("utf-8").splitlines()
    text[2] = text[1]
    (folder / "records.csv").write_text("\n".join(text) + "\n", "utf-8")
    assert "every record once" in check_output(folder, target)


# --- Layout suggestion -----------------------------------------------------------------


def test_nonmem_records_are_suggested_as_event_records():
    header, rows = _table()
    out = E.suggest_layout(header, rows)
    assert out["layout"] == "event_records" and out["reasons"]
    picked = {k: v["column"] for k, v in out["columns"].items()}
    assert picked == {
        "subject": "ID",
        "time": "TIME",
        "record_kind": "EVID",
        "amount": "AMT",
        "endpoint": "CMT",
        "dv": "DV",
        "mdv": "MDV",
        "dose_unit": "EVENTU",
        "time_unit": "TIMEUNIT",
    }
    assert {k["value"]: k["suggested"] for k in out["record_kinds"]} == {
        "0": "observation",
        "1": "dose",
    }
    assert all(k["saved"] is None for k in out["record_kinds"])
    by_code = {e["value"]: e for e in out["endpoints"]}
    assert by_code["1"]["only_on_doses"] is True and by_code["1"]["labels"] == ["Dosing"]
    assert by_code["2"]["labels"] == ["Parent"] and by_code["4"]["units"] == ["IU/L"]
    assert out["needs_analyte"] is False


def test_evid_3_and_4_are_never_given_a_meaning():
    header, rows = _table(PLANTED)
    out = E.suggest_layout(header, rows)
    assert {k["value"]: k["suggested"] for k in out["record_kinds"]}["4"] is None


def test_a_generic_file_is_one_observation_per_row_without_an_analyte():
    header, rows = _table(GENERIC)
    out = E.suggest_layout(header, rows)
    assert out["layout"] == "observation_rows" and out["needs_analyte"] is True
    assert out["unit_hints"] == {"Time (hr)": "h", "Conc.(ng/mL)": "ng/mL"}


def test_an_sdtm_style_file_is_not_event_records():
    header, rows = _table("two_analyte.csv")
    out = E.suggest_layout(header, rows)
    assert out["layout"] == "observation_rows" and out["needs_analyte"] is False


# --- Findings and mapping rules --------------------------------------------------------


def _report(**unresolved):
    counts = dict.fromkeys(PARTITIONS, 0)
    counts["unresolved"] = sum(len(v) for v in unresolved.values())
    return {
        "events_sha256": "0" * 64,
        "rows_in": counts["unresolved"],
        "partitions": counts,
        "doses_used": 0,
        "doses_unused": 0,
        "rows_rejected": 0,
        "unresolved": [{"check": k, "lines": v} for k, v in unresolved.items()],
    }


def test_every_unresolved_group_is_a_blocking_finding_with_its_choice():
    found = E.findings(_report(event_multiple_doses=[3, 4], event_kind_unknown=[9]), "2")
    by = {f["check"]: f for f in found}
    assert by["event_multiple_doses"]["level"] == "blocking"
    assert by["event_multiple_doses"]["choice"] == "dose_interval"
    assert by["event_multiple_doses"]["options"] == ["first", "last", "number"]
    assert "choice" not in by["event_kind_unknown"] and by["event_kind_unknown"]["lines"] == [9]
    assert by["event_accounting"]["level"] == "info"


def test_an_unknown_check_in_events_json_is_refused():
    with pytest.raises(ValueError, match="unknown check"):
        E.findings(_report(event_made_up=[1]), "2")


def test_choices_match_the_schema():
    schema = load_schema("recipe-v1")["$defs"]["events"]["properties"]["choices"]["properties"]
    for choice, options in E.CHOICES.values():
        assert schema[choice]["enum"] == list(options), choice


def test_prepared_columns_are_required_with_events():
    mapping = copy.deepcopy(SETUP["mapping"])
    assert M.problems(mapping, standard_events()) == []
    mapping["roles"].update(ACTUAL_TIME="TIME", DOSE="AMT", ROUTE="route", SUBJECT_ID="NAME")
    mapping["units"]["DOSE"] = {"value": "mg"}
    mapping["roles"]["TAU"] = "NOMTIME"
    found = M.problems(mapping, standard_events())
    assert {(p["check"], p["role"]) for p in found} >= {
        ("event_mapping_mismatch", "ACTUAL_TIME"),
        ("event_mapping_mismatch", "DOSE"),
        ("event_mapping_mismatch", "ROUTE"),
        ("event_mapping_mismatch", "SUBJECT_ID"),
        ("event_role_unsupported", "TAU"),
    }
    assert not [p for p in M.problems(mapping) if p["check"].startswith("event_")]


def _prepared(name: str = MAIN):
    """The prepared table the R stage would write for the standard block (no R here)."""
    header, rows = _table(name)
    lines = EVENTS_EXPECTED["files"][MAIN]["lines"]
    out_header = [*header, *R.EVENT_COLUMNS]
    out = []
    for r in rows:
        e = lines[r[0]]
        if e["partition"] != "selected":
            continue
        out.append(
            [*r, "100", "mg", "extravascular", "0", e["time_after_dose"], "1", e["dose_lines"]]
        )
    return out_header, out


def _run(mapping=None, report=None, analyte="2", events=None):
    header, rows = _prepared()
    events = events or standard_events()
    plan_ = R.check(_doc(events), _header())
    return C.run_checks(
        header,
        rows,
        mapping or SETUP["mapping"],
        C.Selection(analyte),
        lineage=plan_.lineage,
        events=events,
        events_report=report,
    )


def test_the_prepared_rows_raise_no_dose_or_blank_findings():
    # Dose-only rows are not in the prepared table, so nothing reports them.
    out = _run(report=_report())
    checks = {f["check"] for f in out["findings"] if f["level"] == "blocking"}
    assert not checks & {"profile_no_dose", "conc_unclassified_blank", "role_unmapped"}


def test_event_findings_block_every_analyte():
    for analyte in ("2", "3"):
        out = _run(report=_report(event_unmatched=[40]), analyte=analyte)
        blocking = {f["check"] for f in out["findings"] if f["level"] == "blocking"}
        assert "event_unmatched" in blocking
        verdict, _ = C.gate(out["findings"], [], "x")
        assert verdict == "checks_blocking"


def test_a_label_shared_by_two_endpoints_blocks():
    mapping = copy.deepcopy(SETUP["mapping"])
    mapping["roles"]["ANALYTE"] = "EVENTU"  # ng/mL on both PK endpoints
    mapping["roles"].pop("ANALYTE_LABEL")
    out = _run(mapping=mapping, report=_report(), analyte="ng/mL")
    mixed = [f for f in out["findings"] if f["check"] == "analyte_endpoints_mixed"]
    assert mixed and mixed[0]["level"] == "blocking" and mixed[0]["values"] == "2, 3"


def test_nominal_time_is_a_warning_with_event_records():
    mapping = copy.deepcopy(SETUP["mapping"])
    mapping["roles"]["NOMINAL_TIME"] = "NOMTIME"
    out = _run(mapping=mapping, report=_report())
    assert [f["level"] for f in out["findings"] if f["check"] == "event_nominal_basis"] == [
        "warning"
    ]


def test_nominal_event_time_is_blocked_through_lineage():
    events = standard_events()
    events["columns"]["time"] = "NOMTIME"
    out = _run(events=events, report=_report())
    assert "time_nominal_as_actual" in {f["check"] for f in out["findings"]}


# --- ADR, snapshot schemas, API --------------------------------------------------------


def test_every_event_check_is_in_adr_0005_with_its_level():
    text = (ROOT / "docs" / "decisions" / "0005-data-conventions.md").read_text("utf-8")
    table = dict(re.findall(r"^\| `([a-z_]+)` \| (blocking|warning|info) \|", text, re.M))
    for check, level in E.LEVELS.items():
        assert table.get(check) == level, check


def _settings(**extra):
    base = {
        "schema_version": "1.0",
        "run_id": "20261108T000000Z-abcd",
        "created_at": "2026-11-08T00:00:00Z",
        "parent_run_id": None,
        "recipe": {"id": "x", "sha256": "0" * 64, "steps_sha256": "0" * 64},
        "source": {"id": "s", "path": "p", "sha256": "0" * 64, "size_bytes": 1},
        "analyte": {"column": "CMT", "value": "2", "label": "2"},
        "specimen": None,
        "mapping": {"roles": {}},
        "blq": {
            "rules": dict.fromkeys(("blq_predose", "blq_postdose", "blq_terminal"), "exclude"),
            "method": "conventional",
        },
        "acknowledgements": [],
        "snapshot": {"path": "input/analysis.csv", "sha256": "0" * 64, "rows": 1, "columns": {}},
        "row_accounting": {},
    }
    base.update(extra)
    return base


PREPARATION = {
    "kind": "event_records",
    "events_sha256": "0" * 64,
    "rules": {"columns": {}},
    "accounting": {"rows_in": 3, "selected": 1},
    "records": {"path": "input/event-records.csv", "sha256": "0" * 64},
    "script": {"path": "input/prepare.R", "sha256": "0" * 64},
}


def test_settings_carry_a_preparation_only_as_1_1():
    from jsonschema import ValidationError

    validate(_settings(), "run-settings-v1")
    validate(_settings(schema_version="1.1", preparation=PREPARATION), "run-settings-v1")
    with pytest.raises(ValidationError):
        validate(_settings(preparation=PREPARATION), "run-settings-v1")
    with pytest.raises(ValidationError):
        validate(_settings(schema_version="1.1"), "run-settings-v1")


def test_put_events_needs_the_session_secret_and_keeps_the_block(security, tmp_path):
    study = make_study(tmp_path, (MAIN,))
    with nca_client(security, tmp_path, study) as api:
        doc = new_recipe(api, MAIN, SETUP["steps"])
        url = f"/api/nca/recipes/{doc['id']}/events"
        r = api.put(url, json={"events": standard_events()}, headers={"x-opncafyr-session": "x"})
        assert r.status_code == 403
        saved = api.put(url, json={"events": standard_events()}).json()["recipe"]
        assert saved["schema_version"] == "1.1" and saved["events"] == standard_events()
        # A steps save from a page holding the old copy keeps the block.
        r = api.put(f"/api/nca/recipes/{doc['id']}", json={"recipe": doc})
        assert r.status_code == 200 and r.json()["recipe"]["events"] == standard_events()
        bad = standard_events()
        bad["columns"]["time"] = "NOPE"
        assert api.put(url, json={"events": bad}).status_code == 422
        gone = api.put(url, json={"events": None}).json()["recipe"]
        assert gone["schema_version"] == "1.0" and "events" not in gone
