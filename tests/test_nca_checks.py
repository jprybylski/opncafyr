"""Pre-analysis checks against the fixture's planted cases (#24)."""

from __future__ import annotations

import copy
import importlib.util
import json
import sys

import pytest

from nca_helpers import (
    EXPECTED,
    FIXTURES,
    fixture_table,
    project,
    resolving_mapping,
    resolving_steps,
    standard_mapping,
)
from opncafyr.workflows.nca import checks as C
from opncafyr.workflows.nca.recipe import Lineage

ANALYTES = EXPECTED["standard_setup"]["analyte_values"]
SPECIMEN = EXPECTED["standard_setup"]["specimen_value"]


def _findings(
    name="two_analyte.csv", mapping=None, analytes=ANALYTES, specimen=SPECIMEN, table=None, **kw
):
    header, rows = table or fixture_table(name)
    out = []
    for analyte in analytes:
        result = C.run_checks(
            header, rows, mapping or standard_mapping(), C.Selection(analyte, specimen), **kw
        )
        out += result["findings"]
    return out


def _canon(findings):
    return sorted(json.dumps(project(f), sort_keys=True) for f in findings)


def test_levels_follow_adr_0005():
    spec = importlib.util.spec_from_file_location("make_fixtures", FIXTURES / "make_fixtures.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses look their module up here
    spec.loader.exec_module(module)
    for check, level in module.LEVELS.items():
        assert C.LEVELS[check] == level, check
    counts = {lvl: 0 for lvl in ("blocking", "warning", "info")}
    for check, level in C.LEVELS.items():
        if check not in ("flag_table_missing", "column_reused", "opnca_format_check"):
            counts[level] += 1
    assert counts == {"blocking": 21, "warning": 6, "info": 4}


@pytest.mark.parametrize("name", ["two_analyte.csv", "two_analyte_mixed_units.csv"])
def test_planted_cases_give_exactly_the_expected_findings(name):
    got = _findings(name)
    expected = EXPECTED["findings"][name]
    assert _canon(f for f in got if f["level"] != "info") == _canon(
        f for f in expected if f["level"] != "info"
    )
    got_info = _canon(f for f in got if f["level"] == "info")
    for f in _canon(f for f in expected if f["level"] == "info"):
        assert f in got_info


def _includes(findings, inc):
    return any(all(f.get(k) == v for k, v in inc.items()) for f in findings)


def _apply_change(change):
    mapping = copy.deepcopy(standard_mapping())
    for role, column in change.get("mapping", {}).items():
        mapping["roles"][role] = column
        if column is None:
            mapping["roles"].pop(role)
    for role, unit in change.get("units", {}).items():
        if unit is None:
            mapping["units"].pop(role)
    return mapping


MAPPING_SCENARIOS = [
    s for s in EXPECTED["scenarios"]["two_analyte.csv"] if "recipe" not in s["change"]
]


@pytest.mark.parametrize("scenario", MAPPING_SCENARIOS, ids=lambda s: s["id"])
def test_mapping_scenarios(scenario):
    change = scenario["change"]
    mapping = _apply_change(change)
    specimen = SPECIMEN if mapping["roles"].get("SPECIMEN") else None
    analytes = [change.get("analyte_value")] if "analyte_value" in change else scenario["analytes"]
    found = _findings(mapping=mapping, analytes=analytes, specimen=specimen)
    for inc in scenario["includes"]:
        assert _includes(found, inc), (scenario["id"], inc)


def test_specimen_unmapped_is_quiet_for_a_one_specimen_analyte():
    mapping = _apply_change({"mapping": {"SPECIMEN": None}})
    found = _findings(mapping=mapping, analytes=["METAB"], specimen=None)
    assert not any(f["check"] == "specimen_mixed" for f in found)


def test_values_differing_only_in_spaces_or_case_are_other_analytes():
    variant = next(
        v for v in EXPECTED["files"]["two_analyte.csv"]["analytes"]["values"] if v != v.strip()
    )
    header, rows = fixture_table()
    _, acc = C.select(header, rows, standard_mapping(), C.Selection(variant, SPECIMEN))
    assert acc["rows_selected"] == 1
    _, acc = C.select(header, rows, standard_mapping(), C.Selection("Parent", SPECIMEN))
    assert acc["rows_analyte"] == 0


def test_nominal_time_copied_into_a_new_column_is_still_nominal():
    mapping = standard_mapping()
    header, rows = fixture_table()
    i = header.index("NFRLT")
    header = [*header, "T_COPY"]
    rows = [[*r, r[i]] for r in rows]
    mapping["roles"]["ACTUAL_TIME"] = "T_COPY"
    lineage = {"T_COPY": Lineage("NFRLT", ["copy"])}
    found = _findings(mapping=mapping, analytes=["PARENT"], table=(header, rows), lineage=lineage)
    assert _includes(found, {"check": "time_nominal_as_actual", "level": "blocking"})


def _apply_steps(steps):
    """The two ops the resolving recipe uses, in Python (R runs them in the integration
    tests); enough to show the checks honour removals and exclusions."""
    header, rows = fixture_table()
    header = [*header, "opncafyr_excluded", "opncafyr_exclusion_reason"]
    rows = [[*r, "FALSE", ""] for r in rows]
    for step in steps:
        wanted = set(step["args"]["where"]["in"])
        if step["op"] == "filter_rows":
            rows = [r for r in rows if r[0] not in wanted]
        else:
            for r in rows:
                if r[0] in wanted:
                    r[-2], r[-1] = "TRUE", step["args"]["reason"]
    return header, rows


def test_the_resolving_recipe_leaves_only_warnings():
    found = _findings(mapping=resolving_mapping(), table=_apply_steps(resolving_steps()))
    assert [f for f in found if f["level"] == "blocking"] == []
    warnings = {f["check"] for f in found if f["level"] == "warning"}
    assert warnings == {"conc_zero_not_blq", "blq_above_lloq", "lloq_varies", "time_negative"}


def test_gate_blocks_then_needs_every_acknowledgement():
    found = _findings(analytes=["PARENT"])
    verdict, concerned = C.gate(found, [], "sha")
    assert verdict == "checks_blocking" and all(f["level"] == "blocking" for f in concerned)

    clean = _findings(
        mapping=resolving_mapping(), table=_apply_steps(resolving_steps()), analytes=["PARENT"]
    )
    verdict, concerned = C.gate(clean, [], "sha")
    assert verdict == "warnings_unacknowledged" and concerned
    keys = [C.finding_key(f, "sha") for f in concerned]
    assert C.gate(clean, keys[:-1], "sha")[0] == "warnings_unacknowledged"
    assert C.gate(clean, keys, "sha") == ("ok", [])
    # An acknowledgement belongs to one recipe: a changed recipe asks again.
    assert C.gate(clean, keys, "other")[0] == "warnings_unacknowledged"


def test_findings_carry_lines_not_values():
    for f in _findings():
        assert all(isinstance(n, int) for n in f["lines"])
        if f["check"] not in ("flag_value_unmapped",):
            assert "value" not in f


def test_a_time_unit_column_is_checked_like_the_others():
    header, rows = fixture_table()
    header = [*header, "TIMEU"]
    rows = [[*r, "hours" if i % 2 else "min"] for i, r in enumerate(rows)]
    mapping = copy.deepcopy(resolving_mapping())
    mapping["units"]["ACTUAL_TIME"] = {"column": "TIMEU"}
    found = _findings(mapping=mapping, table=(header, rows))
    assert _includes(found, {"check": "units_mixed", "role": "ACTUAL_TIME"})
    rows = [[*r[:-1], "fortnights"] for r in rows]
    found = _findings(mapping=mapping, table=(header, rows))
    assert _includes(found, {"check": "units_undeclared", "role": "ACTUAL_TIME"})
