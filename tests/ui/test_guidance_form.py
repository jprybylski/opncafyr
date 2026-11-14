"""The in-app editor for study profiles and tutorials in a real browser (#76, ADR 0010)."""

from __future__ import annotations

import re

import pytest
from test_home_page import _axe

from opncafyr.core import tutorials as tu
from opncafyr.core.guidance import parse_guidance
from opncafyr.workflows.nca import study_profile as sp
from publish_helpers import writable

pytestmark = pytest.mark.ui

FED_FASTED = "New profile from the example Fed/fasted study (example)"
FIRST = "New tutorial from the example Your first analysis"


@pytest.fixture
def profile_editor(page, home):
    page.goto(page.url.split("#")[0] + "#/profile")
    page.get_by_role("heading", level=1, name="Study profile").wait_for()
    page.get_by_role("button", name=FED_FASTED).click()
    page.get_by_role("heading", name="New study profile").wait_for()
    page.get_by_text("No problems found.").wait_for()
    yield page
    writable(home.study)


@pytest.fixture
def tutorial_editor(page, home):
    page.goto(page.url.split("#")[0] + "#/tutorials")
    page.get_by_role("heading", level=1, name="Tutorials").wait_for()
    page.get_by_role("button", name=FIRST).click()
    page.get_by_role("heading", name="New tutorial").wait_for()
    page.get_by_text("No problems found.").wait_for()
    yield page
    writable(home.study)


def step(page, n):
    return page.locator(f'.guidance-step[data-step="{n - 1}"]')


def focused_label(page):
    return page.evaluate("document.activeElement.getAttribute('aria-label')") or ""


def test_new_profile_reorder_add_check_and_save(profile_editor, home):
    page = profile_editor
    assert page.get_by_text("The example has").is_visible()
    page.get_by_label("File name in profiles/").fill("ours")
    about = page.get_by_role("group", name="About this study profile")
    about.get_by_label("Title (required)").fill("Our fed/fasted study")

    # Step 11 is the publication of the two run slots. Moving it above them, by
    # keyboard, is a problem the check names by step; moving it back clears it.
    step(page, 11).get_by_role("button", name=re.compile(r"^Move up step 11")).focus()
    for _ in range(5):
        page.keyboard.press("Enter")
    assert focused_label(page).startswith("Move up step 6")
    assert step(page, 6).locator("legend").first.inner_text().startswith("Step 6: ")
    problems = page.locator(".guidance-problems")
    problems.get_by_role("button", name="Go to step 6").first.wait_for()
    assert "comes after this step" in problems.inner_text()
    step(page, 6).get_by_role("button", name=re.compile(r"^Move down step 6")).focus()
    for _ in range(5):
        page.keyboard.press("Enter")
    page.get_by_text("No problems found.").wait_for()

    # A new action step: its id follows its title, its control is chosen by title.
    page.get_by_label("New step type").select_option(label="Action (points at a control)")
    page.get_by_role("button", name="Add step").click()
    new = step(page, 13)
    title = new.get_by_label("Title (required)")
    assert page.evaluate("document.activeElement.dataset.field") == "title"
    title.fill("Look at the data")
    assert new.get_by_label("Step id (required)").input_value() == "look-at-the-data"
    new.get_by_label("Text (required)").fill("Open the Data page.")
    new.get_by_label("Control (required)").select_option(label="The list of data files")
    page.get_by_text("No problems found.").wait_for()

    page.get_by_role("button", name="Save as new study profile").click()
    page.get_by_text("Saved profiles/ours.yaml.").wait_for()
    doc, problems = parse_guidance(
        (home.study / "profiles" / "ours.yaml").read_bytes(), sp.SCHEMA, "ours.yaml"
    )
    assert problems == []
    assert doc["title"] == "Our fed/fasted study"
    assert [s["id"] for s in doc["steps"]][-2:] == ["report", "look-at-the-data"]
    assert doc["steps"][-1]["target"] == "shell.data.datasets"
    assert doc["steps"][10]["type"] == "publication"

    page.get_by_role("button", name="Close").click()
    chooser = page.locator("#nca-profile-choose")
    chooser.get_by_label("Profile in profiles/").select_option("ours.yaml")
    assert page.errors == []


def test_a_new_file_never_replaces_one(profile_editor, home):
    page = profile_editor
    (home.study / "profiles").mkdir(exist_ok=True)
    (home.study / "profiles" / "fed-fasted.yaml").write_text("mine\n", "utf-8")
    page.get_by_role("button", name="Save as new study profile").click()
    page.get_by_text("already exists. It was not replaced").first.wait_for()
    assert (home.study / "profiles" / "fed-fasted.yaml").read_text("utf-8") == "mine\n"
    assert page.evaluate("document.activeElement.getAttribute('pattern')")  # the name field


def test_tutorial_view_filters_targets_and_saves(tutorial_editor, home):
    page = tutorial_editor
    page.get_by_label("File name in tutorials/").fill("our-tour")
    first = step(page, 1)
    first.get_by_label("Show this view").select_option(label="Data")
    # The step's control is on Home: still chosen, labelled, and named by the check.
    point = first.get_by_label("Point at")
    assert "on another view" in point.locator("option:checked").inner_text()
    page.locator(".guidance-problems", has_text="on another view than the step").wait_for()
    point.select_option(label="The list of data files")
    page.get_by_text("No problems found.").wait_for()
    page.get_by_role("button", name="Save as new tutorial").click()
    page.get_by_text("Saved tutorials/our-tour.yaml.").wait_for()
    doc, problems = parse_guidance(
        (home.study / "tutorials" / "our-tour.yaml").read_bytes(), tu.SCHEMA, "t"
    )
    assert problems == [] and doc["steps"][0]["target"] == "shell.data.datasets"
    page.get_by_role("button", name="Close").click()
    page.get_by_role("button", name="Edit Your first analysis").wait_for()


def test_quit_saves_the_form_first(profile_editor, home):
    page = profile_editor
    home.jobs.cancel(home.job_id)
    page.get_by_label("File name in profiles/").fill("on-quit")
    about = page.get_by_role("group", name="About this study profile")
    about.get_by_label("Title (required)").fill("Saved on quit")
    page.get_by_role("button", name="Quit").click()
    dialog = page.get_by_role("dialog", name="Unsaved changes")
    dialog.get_by_text("study-profile").wait_for()
    dialog.get_by_role("button", name="Save and quit").click()
    page.get_by_role("heading", level=1, name="opncafyr has stopped").wait_for()
    doc, _ = parse_guidance((home.study / "profiles" / "on-quit.yaml").read_bytes(), sp.SCHEMA, "x")
    assert doc["title"] == "Saved on quit"


def test_close_asks_to_save_or_discard(profile_editor):
    page = profile_editor
    about = page.get_by_role("group", name="About this study profile")
    about.get_by_label("Title (required)").fill("Changed")
    page.get_by_role("button", name="Close").click()
    page.get_by_text("Save or discard your changes first.").wait_for()
    page.get_by_role("button", name="Discard changes").click()
    page.get_by_role("button", name="Close").click()
    page.get_by_role("button", name=FED_FASTED).wait_for()


def test_editors_have_no_axe_violations(profile_editor):
    page = profile_editor
    _axe(page)
    page.get_by_text("Preview the steps").click()
    _axe(page)
    page.get_by_role("button", name="Close").click()
    page.goto(page.url.split("#")[0] + "#/tutorials")
    page.get_by_role("button", name=FIRST).click()
    page.get_by_role("heading", name="New tutorial").wait_for()
    _axe(page)
