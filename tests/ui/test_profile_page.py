"""The Study profile page and guidance targets in a real browser (#68, #69, ADR 0010)."""

from __future__ import annotations

import pytest

from publish_helpers import completed_run, writable

pytestmark = pytest.mark.ui


@pytest.fixture
def profile_page(page, home):
    page.goto(page.url.split("#")[0] + "#/profile")
    page.get_by_role("heading", level=1, name="Study profile").wait_for()
    page.get_by_role(
        "button", name="Copy the example Fed/fasted study (example) into this study"
    ).wait_for()
    yield page
    writable(home.study)


def test_copy_follow_tick_and_resume(profile_page, home):
    page = profile_page
    page.get_by_role(
        "button", name="Copy the example Fed/fasted study (example) into this study"
    ).click()
    page.get_by_text("Copied to profiles/fed-fasted.yaml").wait_for()
    assert (home.study / "profiles" / "fed-fasted.yaml").is_file()

    chooser = page.locator("#nca-profile-choose")
    chooser.get_by_label("Profile in profiles/").select_option("fed-fasted.yaml")
    chooser.get_by_role("button", name="Follow this profile").click()
    steps = page.locator("#nca-profile-steps")
    steps.get_by_text("0 of 12 steps done.").wait_for()

    steps.get_by_role("checkbox", name="Done").first.check()
    steps.get_by_text("1 of 12 steps done.").wait_for()
    steps.get_by_role("checkbox", name="The protocol and analysis plan are at hand.").check()
    page.wait_for_timeout(300)
    assert steps.get_by_role(
        "checkbox", name="The protocol and analysis plan are at hand."
    ).is_checked()

    # Progress is saved by the controller, so a reload keeps it.
    page.reload()
    page.get_by_role("heading", level=1, name="Study profile").wait_for()
    steps.get_by_text("1 of 12 steps done.").wait_for()
    assert steps.get_by_role("checkbox", name="Done").first.is_checked()
    assert page.errors == []


def test_a_slot_takes_the_run_the_user_chooses(profile_page, home):
    page = profile_page
    run_id, _ = completed_run(home.study, "Drug A")
    page.get_by_role(
        "button", name="Copy the example Fed/fasted study (example) into this study"
    ).click()
    chooser = page.locator("#nca-profile-choose")
    chooser.get_by_text("Copied to").wait_for()
    chooser.get_by_label("Profile in profiles/").select_option("fed-fasted.yaml")
    chooser.get_by_role("button", name="Follow this profile").click()
    steps = page.locator("#nca-profile-steps")
    slot = steps.get_by_label("Run for slot fasted")
    slot.wait_for()
    assert slot.input_value() == ""  # nothing is preselected
    slot.select_option(run_id)
    steps.get_by_role("button", name="Use this run").first.click()
    steps.get_by_text("1 of 12 steps done.").wait_for()
    assert page.evaluate("document.activeElement.dataset.key") == "run-fasted:slot"
    assert page.errors == []


def test_go_to_moves_to_the_control(profile_page, home):
    page = profile_page
    page.get_by_role(
        "button", name="Copy the example Fed/fasted study (example) into this study"
    ).click()
    chooser = page.locator("#nca-profile-choose")
    chooser.get_by_text("Copied to").wait_for()
    chooser.get_by_label("Profile in profiles/").select_option("fed-fasted.yaml")
    chooser.get_by_role("button", name="Follow this profile").click()
    steps = page.locator("#nca-profile-steps")
    steps.get_by_text("Hint: The recipe is applied to all rows: not yet.").wait_for()
    steps.get_by_role("button", name="Go to Analysis: 1. Source and recipe").click()
    page.wait_for_url("**#/analysis")
    # The card's heading takes focus once the view is shown.
    focused = None
    for _ in range(30):  # the CSP forbids wait_for_function's eval, so poll
        focused = page.evaluate("document.activeElement.closest('[data-target]')?.dataset.target")
        if focused == "nca.recipe.card":
            break
        page.wait_for_timeout(100)
    assert focused == "nca.recipe.card"
    assert page.errors == []


def test_profile_page_axe(profile_page, home):
    from test_home_page import _axe

    page = profile_page
    page.get_by_role(
        "button", name="Copy the example Drug-drug interaction study (example) into this study"
    ).click()
    chooser = page.locator("#nca-profile-choose")
    chooser.get_by_text("Copied to").wait_for()
    chooser.get_by_label("Profile in profiles/").select_option("drug-interaction.yaml")
    chooser.get_by_role("button", name="Follow this profile").click()
    page.locator("#nca-profile-steps").get_by_text("steps done").wait_for()
    _axe(page)


def test_every_target_of_a_seeded_page_resolves(page):
    # Targets that are on screen in the seeded study (no recipe yet) are found by id.
    catalog = page.evaluate("fetch('api/guidance').then(r => r.json())")
    shown = {
        "home": [
            "shell.nav.home",
            "shell.nav.data",
            "shell.workspace.card",
            "shell.workspace.pick",
            "shell.nav.analysis",
            "shell.nav.publish",
            "shell.nav.profile",
        ],
        "analysis": [
            "nca.recipe.card",
            "nca.recipe.source",
            "nca.steps.card",
            "nca.mapping.card",
            "nca.analyte.card",
            "nca.start.card",
            "nca.runs.card",
        ],
        "publish": [
            "nca.curator.card",
            "nca.curator.check",
            "nca.publications.card",
            "nca.outputs.card",
            "nca.report.card",
            "nca.deck.card",
        ],
        "data": ["shell.data.datasets"],
        "jobs": ["shell.jobs.list"],
    }
    known = {t["id"] for t in catalog["targets"]}
    base = page.url.split("#")[0]
    for view, ids in shown.items():
        page.goto(base + ("#/" if view == "home" else f"#/{view}"))
        page.locator(f"#view-{view} h1").wait_for()
        for target in ids:
            assert target in known, target
            node = page.locator(f'[data-target="{target}"]')
            node.first.wait_for(state="visible")
    assert page.errors == []
