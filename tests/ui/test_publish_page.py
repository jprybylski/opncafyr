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
    boxes.nth(4).wait_for()  # the list is redrawn when the page is shown
    assert boxes.count() == 5  # four pre-selected types and an optional boxplot
    assert [boxes.nth(i).is_checked() for i in range(5)] == [True, True, True, True, False]
    assert curator.get_by_text("Mean concentration–time (linear and log): Not saved").is_visible()
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
    curator.get_by_text("4 file(s) ready to publish.").wait_for()
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
