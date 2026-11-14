"""Tutorials in a real browser (#69, ADR 0010): highlight, directions, recovery, resume."""

from __future__ import annotations

from urllib.parse import urlparse

import pytest

pytestmark = pytest.mark.ui

# Requests the page makes on its own while it is open (lifecycle.js heartbeats).
BACKGROUND = {"/api/session/heartbeat", "/api/session/leave"}


@pytest.fixture
def tour(page):
    page.goto(page.url.split("#")[0] + "#/tutorials")
    page.get_by_role("heading", level=1, name="Tutorials").wait_for()
    page.get_by_role("button", name="Start Your first analysis").wait_for()
    return page


def panel(page):
    return page.locator("#tutorial-panel")


def focused_id(page, wanted):
    for _ in range(30):  # the CSP forbids wait_for_function's eval, so poll
        got = page.evaluate("document.activeElement.id")
        if got == wanted:
            return got
        page.wait_for_timeout(100)
    return got


def highlighted(page, wanted=None):
    """The targets with the highlight ring; with ``wanted``, poll until they match."""
    for _ in range(30):
        got = page.evaluate(
            "[...document.querySelectorAll('.tutorial-target')].map(n => n.dataset.target)"
        )
        if wanted is None or got == wanted:
            return got
        page.wait_for_timeout(100)
    return got


def test_walk_highlight_recover_and_navigate(tour):
    page = tour
    writes = []
    origin = urlparse(page.url).netloc
    page.on(
        "request",
        lambda req: (
            writes.append((req.method, urlparse(req.url)))
            if req.method != "GET" or urlparse(req.url).netloc != origin
            else None
        ),
    )
    page.get_by_role("button", name="Start Your first analysis").click()
    box = panel(page)
    box.get_by_text("Step 1 of 11").wait_for()
    assert page.url.endswith("#/")
    assert highlighted(page, ["shell.workspace.pick"]) == ["shell.workspace.pick"]
    # The folder is open in the seeded study, so Next is enabled.
    assert box.get_by_role("button", name="Next").is_enabled()
    assert focused_id(page, "tutorial-step-title") == "tutorial-step-title"

    box.get_by_role("button", name="Next").click()
    box.get_by_text("Step 2 of 11").wait_for()
    page.wait_for_url("**#/data")
    assert highlighted(page, ["shell.data.datasets"]) == ["shell.data.datasets"]
    # The step moved to another view; focus stays in the panel, not the view's heading.
    assert focused_id(page, "tutorial-step-title") == "tutorial-step-title"

    # Step 3 points at Import, which shows only after a preview: explain and offer a way on.
    box.get_by_role("button", name="Next").click()
    box.get_by_text("Step 3 of 11").wait_for()
    box.get_by_text("isn't on the page right now").wait_for()
    assert box.get_by_role("button", name="Next").is_disabled()  # nothing imported yet
    box.get_by_text("Next is enabled when: the study has an imported data file.").wait_for()
    box.get_by_role("button", name="Continue without highlight").click()
    assert box.get_by_text("isn't on the page right now").count() == 0

    box.get_by_role("button", name="Skip").click()
    box.get_by_text("Step 4 of 11").wait_for()
    page.wait_for_url("**#/analysis")
    assert highlighted(page, ["nca.recipe.card"]) == ["nca.recipe.card"]
    box.get_by_role("button", name="Back").click()
    box.get_by_text("Step 3 of 11").wait_for()
    page.wait_for_url("**#/data")

    # The walk changed nothing but the user's own progress.
    paths = {u.path for m, u in writes}
    assert all(u.netloc == origin for _, u in writes), writes
    assert {m for m, u in writes if u.path not in BACKGROUND} == {"PUT"}
    assert paths - BACKGROUND == {"/api/tutorials/progress"}
    assert page.errors == []


def is_pause_save(response):
    req = response.request
    return (
        req.method == "PUT"
        and urlparse(req.url).path == "/api/tutorials/progress"
        and (req.post_data_json or {}).get("paused") is True
    )


def test_next_then_pause_saves_in_order(tour, home):
    page = tour
    page.get_by_role("button", name="Start Your first analysis").click()
    box = panel(page)
    box.get_by_text("Step 1 of 11").wait_for()
    with page.expect_response(is_pause_save):
        box.get_by_role("button", name="Next").click()
        box.get_by_role("button", name="Pause").click()
    # The pause save was sent after the Next save finished, so it is the one kept.
    saved = page.request.get(page.url.split("#")[0] + "api/tutorials").json()
    [progress] = saved["progress"]["tutorials"].values()
    assert progress["paused"] is True and progress["step"] == "data"


def test_pause_resume_after_reload_and_exit(tour, home):
    page = tour
    page.get_by_role("button", name="Start Your first analysis").click()
    box = panel(page)
    box.get_by_role("button", name="Next").click()
    box.get_by_text("Step 2 of 11").wait_for()
    with page.expect_response(is_pause_save):
        box.get_by_role("button", name="Pause").click()
    box.get_by_text("Paused. Resume to carry on from this step.").wait_for()
    assert highlighted(page, []) == []

    # Progress is saved by the controller: a reload (or a new launch) comes back paused.
    page.reload()
    box.get_by_role("button", name="Resume").wait_for()
    assert box.is_visible() and highlighted(page) == []
    box.get_by_role("button", name="Resume").click()
    box.get_by_text("Step 2 of 11").wait_for()
    assert highlighted(page, ["shell.data.datasets"]) == ["shell.data.datasets"]

    page.locator("#tutorial-step-title").focus()
    page.keyboard.press("Escape")
    box.wait_for(state="hidden")
    assert highlighted(page, []) == []
    page.goto(page.url.split("#")[0] + "#/tutorials")
    page.get_by_text("Stopped at step “data”.").wait_for()
    page.get_by_role("button", name="Resume Your first analysis").click()
    box.get_by_text("Step 2 of 11").wait_for()
    box.get_by_role("button", name="Exit tutorial").click()
    box.wait_for(state="hidden")
    page.goto(page.url.split("#")[0] + "#/tutorials")  # Exit leaves you where you are
    page.get_by_role("button", name="Reset progress of Your first analysis").click()
    page.get_by_text("Progress of Your first analysis reset.").wait_for()
    page.get_by_role("button", name="Start Your first analysis").wait_for()
    assert page.errors == []


def test_copy_into_the_study(tour, home):
    page = tour
    page.get_by_role("button", name="Copy Your first analysis into this study").click()
    page.get_by_text("Copied to tutorials/first-analysis.yaml").wait_for()
    assert (home.study / "tutorials" / "first-analysis.yaml").is_file()
    page.get_by_role("button", name="tutorials/first-analysis.yaml exists").wait_for()
    assert page.errors == []


def test_tutorials_page_and_panel_axe(tour):
    from test_home_page import _axe

    page = tour
    _axe(page)
    page.get_by_role("button", name="Start Your first analysis").click()
    panel(page).get_by_text("Step 1 of 11").wait_for()
    _axe(page)
