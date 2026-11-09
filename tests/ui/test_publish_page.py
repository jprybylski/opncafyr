"""The Publish page in a real browser (#30, #31): pick, check, publish, list; axe."""

from __future__ import annotations

import re

import pytest

from publish_helpers import completed_run, writable

pytestmark = pytest.mark.ui


@pytest.fixture
def published_page(page, home):
    run_id, _ = completed_run(home.study, "Drug A")
    page.goto(page.url.split("#")[0] + "#/publish")
    page.get_by_role("heading", level=1, name="Publish").wait_for()
    yield page, run_id
    writable(home.study)


def test_publish_from_the_page(published_page):
    page, run_id = published_page
    curator = page.locator("#nca-curator")
    curator.get_by_text(run_id).wait_for()
    boxes = curator.get_by_role("checkbox")
    boxes.nth(7).wait_for()  # the list is redrawn when the page is shown
    # The four ADR 0007 types (six files), then an optional boxplot and the mean table.
    assert boxes.count() == 8
    assert [boxes.nth(i).is_checked() for i in range(8)] == [True] * 6 + [False] * 2
    assert curator.get_by_text("saved without the mean figures").count() == 0
    for name in (
        "Mean concentration–time profiles (linear)",
        "Mean concentration–time profiles (log)",
    ):
        curator.get_by_role("checkbox", name=re.compile(re.escape(name))).uncheck()
    publish = curator.get_by_role("button", name="Publish")
    assert publish.is_disabled()
    curator.get_by_role("button", name="Check").click()
    curator.get_by_text("4 file(s) ready to publish.").wait_for()
    assert curator.locator("table").get_by_text("New copy").count() == 4
    publish.click()
    done = curator.get_by_text(re.compile(r"Recorded pub-"))
    done.wait_for()
    pub_id = re.search(r"pub-\d{8}T\d{6}Z-[0-9a-f]{4}", done.inner_text()).group(0)
    page.locator("#nca-publications").get_by_text(pub_id).wait_for()
    outputs = page.locator("#nca-outputs")
    outputs.get_by_role("button", name="Check published files").click()
    outputs.get_by_text("every sidecar matches").wait_for()
    assert page.errors == []


def test_a_run_without_the_mean_figures_says_so(published_page, home):
    page, _ = published_page
    older, _ = completed_run(home.study, "Drug B", mean=False)
    page.reload()
    curator = page.locator("#nca-curator")
    curator.get_by_text(older).wait_for()
    note = curator.get_by_text(re.compile("Mean concentration–time .*saved without the mean"))
    note.wait_for()
    assert note.count() == 1
    assert page.errors == []


def test_publish_page_axe(published_page):
    from test_home_page import _axe

    page, run_id = published_page
    page.locator("#nca-curator").get_by_text(run_id).wait_for()
    _axe(page)


def test_report_card_without_r(published_page, home):
    page, run_id = published_page
    curator = page.locator("#nca-curator")
    curator.get_by_text(run_id).wait_for()
    curator.get_by_role("button", name="Check").click()
    curator.get_by_text("6 file(s) ready to publish.").wait_for()
    curator.get_by_role("button", name="Publish").click()
    done = curator.get_by_text(re.compile(r"Recorded pub-"))
    done.wait_for()
    pub_id = re.search(r"pub-\d{8}T\d{6}Z-[0-9a-f]{4}", done.inner_text()).group(0)
    card = page.locator("#nca-report")
    card.get_by_text("No report project yet.").wait_for()
    assert card.get_by_role("button", name="Render").is_disabled()
    card.get_by_label("Publication").select_option(pub_id)
    card.get_by_role("button", name="Set up report").click()
    # The harness's toy job holds the job pool; the setup waits its turn.
    card.get_by_text(re.compile(r"Waiting to start")).wait_for()
    home.jobs.cancel(home.job_id)
    # No R in this harness: the job fails with a message for a person, and nothing is made.
    card.get_by_text(re.compile(r"not available")).wait_for(timeout=30000)
    assert card.get_by_text("No report project yet.").is_visible()
    assert page.errors == []


def test_deck_card_without_tools(published_page, home):
    page, run_id = published_page
    card = page.locator("#nca-deck")
    card.get_by_text("No deck project yet.").wait_for()
    assert card.get_by_role("button", name="Build").is_disabled()
    assert card.get_by_role("button", name="Open editor").is_disabled()
    assert card.get_by_role("checkbox", name="with slide previews").is_disabled()
    assert page.errors == []


def test_a_failed_publications_list_does_not_outlive_a_later_success(published_page):
    # The list is requested when the page opens, before a study folder may be open. A failed
    # request showed an error that no later success cleared (#62).
    page, _ = published_page
    publications = page.locator("#nca-publications")
    calls = {"n": 0}

    def fail_once(route):
        calls["n"] += 1
        if calls["n"] == 1:
            route.fulfill(
                status=409,
                content_type="application/json",
                body='{"detail": {"code": "other", "message": "Not ready yet."}}',
            )
        else:
            route.continue_()

    page.route("**/api/nca/publications", fail_once)
    page.goto(page.url.split("#")[0] + "#/")
    page.goto(page.url.split("#")[0] + "#/publish")
    publications.get_by_text("Not ready yet.").wait_for()
    page.goto(page.url.split("#")[0] + "#/")
    page.goto(page.url.split("#")[0] + "#/publish")
    publications.get_by_role("table").wait_for()  # the home study already lists one
    assert publications.get_by_text("Not ready yet.").count() == 0
    assert publications.get_by_text("Error:").count() == 0
