"""The Analysis page in a real browser: editing a saved cleaning step (#63).

The recipe has one step of every kind the page can add, so each kind's form is checked to
open on its saved arguments and to write the same step back.
"""

from __future__ import annotations

import copy

import pytest

pytestmark = pytest.mark.ui


def no_script_errors(page) -> None:
    # The page asks for results that don't exist yet and gets 404 or 409 on purpose; the
    # browser logs those. Only script errors count here.
    assert [e for e in page.errors if not e.startswith("Failed to load resource")] == []


SOURCE = "source-data/two_analyte.csv"

ALL_KINDS = [
    {"op": "trim", "args": {"columns": ["PCTESTCD", "PCSPEC"]}, "note": "stray spaces"},
    {"op": "derive", "args": {"fn": "copy", "target": "SPECX", "from": "PCSPEC"}},
    {
        "op": "derive",
        "args": {"fn": "concat", "target": "KEY", "from": ["USUBJID", "PCTESTCD"], "sep": "|"},
    },
    {"op": "derive", "args": {"fn": "constant", "target": "NOTE", "value": "x"}},
    {"op": "recode", "args": {"column": "PCTESTCD", "map": {"PARENT": "PARENT", "METAB": "METAB"}}},
    {"op": "parse_type", "args": {"column": "AFRLT", "type": "number", "decimal_mark": "."}},
    {
        "op": "flag",
        "args": {"target": "IS_BLQ", "column": "BLQFL", "table": {"Y": True, "": False}},
    },
    {
        "op": "derive_elapsed_time",
        "args": {
            "target": "ELAPSED",
            "from": "PCDTC",
            "reference": "EXSTDTC",
            "unit": "h",
            "time_zone": "both are local time at the site",
        },
    },
    {
        "op": "convert_units",
        "args": {"value_column": "CONC", "unit_column": "PCSTRESU", "from": "ng/mL", "to": "ug/L"},
    },
    {
        "op": "exclude",
        "args": {"where": {"column": "PCSPEC", "equals": "URINE"}, "reason": "Not plasma"},
        "note": "plasma only",
    },
    {
        "op": "exclude",
        "args": {"where": {"column": "USUBJID", "in": ["TA-001", "TA-002"]}, "reason": "Withdrawn"},
    },
    {
        "op": "filter_rows",
        "args": {"where": {"column": "CONC", "is_blank": True}, "reason": "No value"},
    },
    {"op": "report_dupes", "args": {"columns": ["USUBJID", "PCTESTCD", "AFRLT"]}},
    {"op": "clean_names", "args": {}},
]


class Api:
    """The page's own session, used to set up and to read back what the page saved."""

    def __init__(self, page, base: str):
        self.page, self.base = page, base
        session = page.locator('meta[name="opncafyr-session"]').get_attribute("content")
        self.headers = {"x-opncafyr-session": session, "origin": base}

    def call(self, method: str, path: str, body=None):
        r = self.page.request.fetch(
            self.base + path, method=method, headers=self.headers, data=body
        )
        return r.status, r.json()

    def make_recipe(self, steps: list) -> str:
        status, _ = self.call("POST", "/api/datasets/import", {"path": SOURCE})
        assert status in (200, 201, 409), status
        _, listed = self.call("GET", "/api/nca/sources")
        source = next(s["id"] for s in listed["sources"] if s["path"] == SOURCE)
        _, made = self.call("POST", "/api/nca/recipes", {"source_id": source})
        doc = made["recipe"]
        doc["steps"] = steps
        status, saved = self.call("PUT", f"/api/nca/recipes/{doc['id']}", {"recipe": doc})
        assert status == 200, saved
        return doc["id"]

    def recipe(self, recipe_id: str) -> dict:
        _, got = self.call("GET", f"/api/nca/recipes/{recipe_id}")
        return got


@pytest.fixture
def analysis(page, home):
    def open_with(steps: list):
        api = Api(page, home.url.rstrip("/"))
        recipe_id = api.make_recipe(steps)
        # The page lists sources when it loads, so load it after the setup.
        page.goto(home.url.rstrip("/") + "/#/analysis")
        page.reload()
        page.get_by_role("heading", level=1, name="Analysis").wait_for()
        section = page.locator("#nca-recipe")
        section.get_by_label("Imported file").select_option(label=SOURCE)
        section.get_by_label("Recipe").select_option(value=recipe_id)
        page.locator("#nca-steps ol li").first.wait_for()
        return api, recipe_id

    return open_with


def step_text(page, n: int) -> str:
    return page.locator("#nca-steps ol > li").nth(n - 1).inner_text()


def test_every_kind_of_step_opens_on_its_saved_values_and_saves_back_unchanged(analysis, page):
    # The recipe is compared as the server stores it: opening a step and saving it without a
    # change must leave the recipe, and so its hash, exactly as it was.
    api, rid = analysis(copy.deepcopy(ALL_KINDS))
    before = api.recipe(rid)
    assert len(before["recipe"]["steps"]) == len(ALL_KINDS)
    for n in range(1, len(ALL_KINDS) + 1):
        page.get_by_role("button", name=f"Edit step {n}", exact=True).click()
        form = page.locator("#nca-steps .nca-form")
        form.get_by_text(f"Editing step {n}:").wait_for()
        form.get_by_role("button", name="Save step").click()
        form.wait_for(state="detached")
        now = api.recipe(rid)
        assert now["recipe"]["steps"] == before["recipe"]["steps"], f"step {n} changed"
        assert now["sha256"] == before["sha256"], f"step {n}: the hash changed"
    no_script_errors(page)


def test_edit_changes_one_step_in_place_and_leaves_the_others(analysis, page):
    api, rid = analysis(copy.deepcopy(ALL_KINDS))
    before = api.recipe(rid)
    page.get_by_role("button", name="Edit step 10", exact=True).click()
    form = page.locator("#nca-steps .nca-form")
    heading = form.get_by_text("Editing step 10: Exclude rows")
    heading.wait_for()
    assert "Editing step 10" in page.evaluate("document.activeElement.textContent")
    # The saved values are in the fields.
    assert form.get_by_label("Value or values, exact text").input_value() == "URINE"
    assert form.get_by_label("Reason").input_value() == "Not plasma"
    assert form.get_by_label("Note (optional)").input_value() == "plasma only"
    # Only one step is open: the others can't be moved or removed meanwhile.
    assert page.get_by_role("button", name="Remove step 3", exact=True).is_disabled()
    form.get_by_label("Reason").fill("Urine is not part of this analysis")
    form.get_by_role("button", name="Save step").click()
    form.wait_for(state="detached")
    after = api.recipe(rid)
    steps = after["recipe"]["steps"]
    assert after["sha256"] != before["sha256"]
    assert len(steps) == len(ALL_KINDS)
    assert steps[9]["args"]["reason"] == "Urine is not part of this analysis"
    assert steps[9]["args"]["where"] == ALL_KINDS[9]["args"]["where"]
    assert steps[9]["note"] == "plasma only"
    assert [s for i, s in enumerate(steps) if i != 9] == [
        s for i, s in enumerate(before["recipe"]["steps"]) if i != 9
    ]
    assert "Urine is not part of this analysis" in step_text(page, 10)
    # The page asks for a fresh preview and checks, and focus comes back to the step.
    page.get_by_text("The recipe changed.").wait_for()
    assert page.evaluate("document.activeElement.getAttribute('aria-label')") == "Edit step 10"
    no_script_errors(page)


def test_cancel_changes_nothing(analysis, page):
    api, rid = analysis(copy.deepcopy(ALL_KINDS))
    before = api.recipe(rid)
    page.get_by_role("button", name="Edit step 4", exact=True).click()
    form = page.locator("#nca-steps .nca-form")
    form.get_by_label("Value").fill("something else")
    form.get_by_role("button", name="Cancel").click()
    form.wait_for(state="detached")
    assert api.recipe(rid)["sha256"] == before["sha256"]
    assert '"x"' in step_text(page, 4)
    assert page.evaluate("document.activeElement.getAttribute('aria-label')") == "Edit step 4"


def test_a_refused_edit_keeps_the_draft_open_with_the_reason(analysis, page):
    # Step 3 uses the column step 2 makes; renaming that column breaks step 3, which the
    # server refuses. The form stays, with what was typed, and nothing is saved.
    steps = [
        {"op": "derive", "args": {"fn": "copy", "target": "SPECX", "from": "PCSPEC"}},
        {"op": "exclude", "args": {"where": {"column": "SPECX", "equals": "URINE"}, "reason": "r"}},
    ]
    api, rid = analysis(steps)
    before = api.recipe(rid)
    page.get_by_role("button", name="Edit step 1", exact=True).click()
    form = page.locator("#nca-steps .nca-form")
    form.get_by_label("New column").fill("SPECY")
    form.get_by_label("Note (optional)").fill("draft text to keep")
    form.get_by_role("button", name="Save step").click()
    form.locator(".status.error").wait_for()
    assert "SPECX" in form.locator(".status").inner_text()
    assert form.get_by_label("New column").input_value() == "SPECY"
    assert form.get_by_label("Note (optional)").input_value() == "draft text to keep"
    assert form.is_visible()
    assert api.recipe(rid)["sha256"] == before["sha256"]
    form.get_by_role("button", name="Cancel").click()
    no_script_errors(page)


def test_a_step_the_form_cannot_show_is_not_editable(analysis, page):
    both = {
        "all": [
            {"column": "PCSPEC", "equals": "URINE"},
            {"column": "USUBJID", "equals": "TA-001"},
        ]
    }
    steps = [{"op": "exclude", "args": {"where": both, "reason": "Both"}}]
    api, rid = analysis(steps)
    edit = page.get_by_role("button", name="Edit step 1", exact=True)
    assert edit.is_disabled()
    assert "combines several conditions" in page.locator("#nca-steps ol").inner_text()
    no_script_errors(page)


def test_the_edit_form_has_no_axe_violations(analysis, page):
    from test_home_page import _axe

    analysis(copy.deepcopy(ALL_KINDS))
    page.get_by_role("button", name="Edit step 5", exact=True).click()
    page.locator("#nca-steps .nca-form").wait_for()
    _axe(page)


# --- findings pinned under the steps (#63) ----------------------------------------------

FINDINGS = [
    {
        "level": "blocking",
        "check": "duplicate_time",
        "key": "k-block",
        "lines": [4, 9],
        "message": "Two samples share one time for subject TA-003.",
        "subject": "TA-003",
        "fix_hint": "Remove one of the two rows with a cleaning step, with a reason.",
    },
    {
        "level": "warning",
        "check": "blq_after_dose",
        "key": "k-warn",
        "lines": [12],
        "message": "A BLQ value follows the last quantifiable sample.",
    },
    {
        "level": "info",
        "check": "units",
        "key": "k-info",
        "lines": [],
        "message": "Units from PCSTRESU.",
    },
]


def fake_checks(page, api):
    """Stand-ins for the two calls that need R: the analyte list and the checks themselves."""

    def recipe_id(route) -> str:
        return route.request.url.split("/api/nca/recipes/")[1].split("/")[0]

    def analytes(route):
        route.fulfill(
            json={
                "values": [
                    {
                        "value": "PARENT",
                        "rows": 10,
                        "specimens": {"PLASMA": 10},
                        "labels": {"Parent drug": 10},
                    },
                ],
                "specimen_column": "PCSPEC",
            }
        )

    def checks(route):
        route.fulfill(
            json={
                "findings": copy.deepcopy(FINDINGS),
                "summary": {"blocking": 1, "warning": 1, "info": 1},
                "accounting": {},
                "recipe_sha256": api.recipe(recipe_id(route))["sha256"],
                "format": {"job_id": None},
            }
        )

    page.route("**/api/nca/recipes/*/analytes", analytes)
    page.route("**/api/nca/recipes/*/checks", checks)


def run_checks(page):
    pick = page.locator("#nca-analyte")
    pick.get_by_label("Analyte (exact value)").select_option(value="PARENT")
    pick.get_by_label("Specimen", exact=False).select_option(value="PLASMA")
    pick.get_by_role("button", name="Run checks").click()
    pick.get_by_text("1 blocking, 1 to acknowledge, 1 notes.").wait_for()


@pytest.fixture
def checked(analysis, page):
    api = Api(page, page.url.split("#")[0].rstrip("/"))
    fake_checks(page, api)
    api, rid = analysis(copy.deepcopy(ALL_KINDS)[:4])
    run_checks(page)
    return api, rid


def test_findings_are_pinned_under_the_steps_without_acknowledgements(checked, page):
    pinned = page.locator("#nca-steps details.nca-pinned")
    pinned.wait_for()
    pinned.locator("summary").click()
    assert "“PARENT”" in pinned.locator("summary").inner_text()
    assert "1 blocking, 1 to acknowledge" in pinned.locator("summary").inner_text()
    text = pinned.inner_text()
    assert "Two samples share one time for subject TA-003." in text
    assert "Remove one of the two rows with a cleaning step, with a reason." in text  # the hint
    assert "A BLQ value follows the last quantifiable sample." in text
    assert "Units from PCSTRESU." not in text  # notes stay in the full list
    assert pinned.get_by_role("checkbox").count() == 0  # acknowledging stays in section 5
    # It sits below the step adder, inside the steps section.
    assert page.evaluate(
        "document.querySelector('#nca-steps select[aria-label=\"Add a step\"]')"
        ".compareDocumentPosition(document.querySelector('#nca-steps details.nca-pinned')) & 4"
    )
    # Back to the full list: focus lands on section 5's heading.
    pinned.get_by_role("button", name="Back to the full findings").click()
    assert "Analyte and checks" in page.evaluate("document.activeElement.textContent")
    no_script_errors(page)


def test_the_pinned_panel_works_from_the_keyboard(checked, page):
    pinned = page.locator("#nca-steps details.nca-pinned")
    pinned.wait_for()
    summary = pinned.locator("summary")
    was_open = pinned.evaluate("d => d.open")
    summary.focus()
    page.keyboard.press("Enter")
    assert pinned.evaluate("d => d.open") != was_open
    page.keyboard.press("Enter")
    assert pinned.evaluate("d => d.open") == was_open


def test_editing_a_step_makes_the_findings_out_of_date_everywhere(checked, page):
    pinned = page.locator("#nca-steps details.nca-pinned")
    pinned.locator("summary").click()
    pinned.get_by_text("Two samples share one time").wait_for()
    # Acknowledge the warning in section 5, then change a step.
    full = page.locator("#nca-findings")
    full.get_by_role("checkbox").check()
    page.get_by_role("button", name="Edit step 4", exact=True).click()
    form = page.locator("#nca-steps .nca-form")
    form.get_by_label("Value").fill("changed")
    form.get_by_role("button", name="Save step").click()
    form.wait_for(state="detached")
    # Pinned: marked out of date, findings hidden, with the way forward.
    pinned.get_by_text("Out of date").wait_for()
    assert "Two samples share one time" not in pinned.inner_text()
    assert "run the checks again" in pinned.inner_text()
    # Section 5 says the same, and nothing can start.
    assert "earlier version of the recipe or mapping" in full.inner_text()
    assert full.get_by_text("Two samples share one time").count() == 0
    assert page.locator("#nca-start").get_by_text("run the checks again").is_visible()
    assert page.locator("#nca-start").get_by_role("button", name="Start analysis").is_disabled()
    # A new check shows current findings; the earlier acknowledgement did not carry over.
    run_checks(page)
    assert page.locator("#nca-findings").get_by_role("checkbox").is_checked() is False
    assert pinned.get_by_text("Out of date").count() == 0
    pinned.get_by_text("Two samples share one time").wait_for()
    no_script_errors(page)


def test_the_pinned_panel_fits_a_narrow_screen_and_has_no_axe_violations(checked, page):
    from test_home_page import _axe

    pinned = page.locator("#nca-steps details.nca-pinned")
    pinned.locator("summary").click()
    page.set_viewport_size({"width": 360, "height": 740})
    pinned.get_by_text("Two samples share one time").wait_for()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
    _axe(page)
