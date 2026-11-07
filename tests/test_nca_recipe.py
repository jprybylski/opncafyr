"""Cleaning recipes: schema, rules, hashing and the compiled R script (#22)."""

from __future__ import annotations

import copy
import re

import pytest

from nca_helpers import FIXTURES
from opncafyr.workflows.nca import recipe as R
from opncafyr.workflows.nca.clean_compile import compile_recipe, r_string, read_header
from opncafyr.workflows.nca.recipe import RecipeError

HEADER = read_header(FIXTURES / "two_analyte.csv", ",")


def doc(*steps):
    d = R.new_recipe("ta-1", "two-analyte-abc")
    d["steps"] = list(steps)
    return d


EVERY_OP = [
    {"op": "clean_names", "args": {}},
    {"op": "rename", "args": {"from": "pcspec", "to": "specimen"}},
    {"op": "trim", "args": {"columns": ["pctestcd"]}},
    {"op": "parse_type", "args": {"column": "conc", "type": "number", "decimal_mark": ","}},
    {"op": "recode", "args": {"column": "route", "map": {"EXTRAVASULAR": "extravascular"}}},
    {"op": "derive", "args": {"target": "copy_t", "fn": "copy", "from": "afrlt"}},
    {
        "op": "derive",
        "args": {"target": "key", "fn": "concat", "from": ["usubjid", "afrlt"], "sep": "|"},
    },
    {"op": "derive", "args": {"target": "site", "fn": "constant", "value": "A"}},
    {
        "op": "derive_elapsed_time",
        "args": {
            "target": "eltm",
            "from": "pcdtc",
            "reference": "exstdtc",
            "unit": "h",
            "time_zone": "all local",
        },
    },
    {
        "op": "convert_units",
        "args": {"value_column": "conc", "unit_column": "pcstresu", "from": "ug/L", "to": "ng/mL"},
    },
    {"op": "flag", "args": {"target": "blq", "column": "blqfl", "table": {"Y": True, "": False}}},
    {
        "op": "exclude",
        "args": {"where": {"column": "opncafyr_line", "equals": "24"}, "reason": "site note"},
    },
    {
        "op": "filter_rows",
        "args": {
            "where": {
                "all": [
                    {"column": "usubjid", "in": ["TA-001"]},
                    {"column": "conc", "is_blank": True},
                ]
            },
            "reason": "not analysed",
        },
    },
    {"op": "report_dupes", "args": {"columns": ["usubjid", "afrlt"]}},
]


def test_every_op_is_valid_in_order():
    plan = R.check(doc(*EVERY_OP), HEADER)
    assert plan.columns[0] == "opncafyr_line"
    for name in (
        "specimen",
        "copy_t",
        "key",
        "site",
        "eltm",
        "blq",
        "opncafyr_excluded",
        "opncafyr_exclusion_reason",
    ):
        assert name in plan.columns


@pytest.mark.parametrize(
    "step",
    [
        {"op": "drop_rows", "args": {}},
        {"op": "rename", "args": {"from": "A"}},
        {"op": "rename", "args": {"from": "A", "to": "B", "expr": "x"}},
        {"op": "derive", "args": {"target": "X", "fn": "eval", "from": "A"}},
        {"op": "filter_rows", "args": {"where": {"column": "A", "equals": "1"}}},
        {"op": "filter_rows", "args": {"where": {"column": "A", "equals": "1"}, "reason": " "}},
        {"op": "exclude", "args": {"where": {"column": "A", "regex": ".*"}, "reason": "r"}},
    ],
)
def test_schema_rejects_unknown_shapes(step):
    with pytest.raises(RecipeError) as err:
        R.check(doc(step), HEADER)
    assert err.value.step == 1


@pytest.mark.parametrize("blq", ["<LLOQ", "< 0.05", "BLQ", "bloq", "BQL", "", "NA", "."])
def test_no_op_sets_blq_or_blank_to_zero(blq):
    for zero in ("0", "0.0", "0e0", "-0"):
        step = {"op": "recode", "args": {"column": "CONC", "map": {blq: zero}}}
        with pytest.raises(RecipeError, match="never done"):
            R.check(doc(step), HEADER)


def test_no_constant_zero_column():
    step = {"op": "derive", "args": {"target": "C0", "fn": "constant", "value": "0"}}
    with pytest.raises(RecipeError, match="never derived"):
        R.check(doc(step), HEADER)


@pytest.mark.parametrize(
    "steps",
    [
        # copy nominal time over actual time
        [{"op": "derive", "args": {"target": "AFRLT", "fn": "copy", "from": "NFRLT"}}],
        # rename nominal onto actual
        [{"op": "rename", "args": {"from": "NFRLT", "to": "AFRLT"}}],
        # the swap: move actual away, then rename nominal into its old name
        [
            {"op": "rename", "args": {"from": "AFRLT", "to": "OLD_TIME"}},
            {"op": "rename", "args": {"from": "NFRLT", "to": "AFRLT"}},
        ],
        # a flag can't overwrite a column either
        [{"op": "flag", "args": {"target": "AFRLT", "column": "BLQFL", "table": {"Y": True}}}],
    ],
)
def test_no_op_overwrites_actual_time(steps):
    with pytest.raises(RecipeError, match="is, or was, a column name"):
        R.check(doc(*steps), HEADER)


def test_copy_lineage_is_kept_for_the_nominal_as_actual_check():
    plan = R.check(
        doc(
            {"op": "derive", "args": {"target": "T", "fn": "copy", "from": "NFRLT"}},
            {"op": "clean_names", "args": {}},
        ),
        HEADER,
    )
    assert plan.lineage["t"].origin == "NFRLT"
    assert plan.lineage["afrlt"].origin == "AFRLT"


@pytest.mark.parametrize(
    "step",
    [
        {"op": "recode", "args": {"column": "opncafyr_line", "map": {"2": "3"}}},
        {"op": "trim", "args": {"columns": ["opncafyr_excluded"]}},
        {"op": "rename", "args": {"from": "opncafyr_line", "to": "line"}},
    ],
)
def test_launcher_columns_are_protected(step):
    steps = [
        {"op": "exclude", "args": {"where": {"column": "USUBJID", "equals": "x"}, "reason": "r"}},
        step,
    ]
    with pytest.raises(RecipeError, match="managed by the launcher"):
        R.check(doc(*steps), HEADER)


def test_missing_column_names_the_step():
    with pytest.raises(RecipeError) as err:
        R.check(
            doc(
                {"op": "clean_names", "args": {}}, {"op": "trim", "args": {"columns": ["USUBJID"]}}
            ),
            HEADER,
        )
    assert err.value.step == 2 and "no column USUBJID" in str(err.value)


def test_unknown_unit_conversion_is_refused():
    step = {
        "op": "convert_units",
        "args": {
            "value_column": "CONC",
            "unit_column": "PCSTRESU",
            "from": "ng/mL",
            "to": "nmol/L",
        },
    }
    with pytest.raises(RecipeError, match="not a conversion"):
        R.check(doc(step), HEADER)


def test_hash_is_canonical():
    a = doc(*EVERY_OP[:3])
    b = copy.deepcopy(a)
    b = {k: b[k] for k in reversed(list(b))}
    assert R.recipe_hash(a) == R.recipe_hash(b)
    b["steps"][1]["args"]["to"] = "spec"
    assert R.recipe_hash(a) != R.recipe_hash(b)


def test_yaml_round_trip():
    d = doc(*EVERY_OP)
    assert R.loads(R.dumps(d)) == d


@pytest.mark.parametrize(
    ("names", "expected"),
    [
        (
            ["USUBJID", "Conc (ng/mL)", "1st dose", "µg", "a", "A"],
            ["usubjid", "conc_ng_ml", "x1st_dose", "g", "a", "a_2"],
        ),
        (["opncafyr_line", "PC TEST"], ["opncafyr_line", "pc_test"]),
    ],
)
def test_clean_names(names, expected):
    assert R.clean_names(names) == expected


HOSTILE = [
    'a"); system("touch pwned"); x <- ("',
    "back\\slash",
    "new\nline\r\t",
    "µg/mL — ünïcode 😀",
    "`backtick` $x ${y}",
]


@pytest.mark.parametrize("text", HOSTILE)
def test_r_string_is_inert_ascii(text):
    lit = r_string(text)
    assert lit.startswith('"') and lit.endswith('"')
    assert all(0x20 <= ord(c) < 0x7F for c in lit)
    # Every quote inside is escaped, so the literal can't end early.
    assert re.fullmatch(r'"(?:[^"\\]|\\.)*"', lit)


def test_r_string_refuses_nul():
    with pytest.raises(ValueError):
        r_string("a\x00b")


def test_compiled_script_has_no_recipe_text_outside_literals():
    hostile = 'evil"); system("touch pwned'
    step = {
        "op": "recode",
        "args": {"column": "ROUTE", "map": {hostile: hostile}},
        "note": "x\nsystem('touch pwned')",
    }
    script = compile_recipe(doc(step), source_rel="source-data/two_analyte.csv", delimiter=",")
    recipe_part = script.split("# --- This recipe")[1]
    code = re.sub(r'"(?:[^"\\]|\\.)*"', '""', recipe_part)
    assert "pwned" not in code and "system" not in code
    assert "source(" not in script  # the library is inlined, never sourced from a path
    assert script.count("oc_run <- function") == 1
