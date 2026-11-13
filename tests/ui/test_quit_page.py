"""After Quit, in a real browser (#73): finishing, stopped and connection-lost pages.

The fixture's launcher records a stop instead of closing its listener, so a lost connection
is simulated by refusing the page's API requests.
"""

from __future__ import annotations

import time

import pytest
from home_seed import JOB_LABEL
from test_home_page import FOCUSABLE, _axe

pytestmark = pytest.mark.ui

STOPPED = "opncafyr has stopped"
LOST = "Connection to opncafyr was lost"


def lifecycle(page) -> dict:
    return page.evaluate("fetch('api/lifecycle').then((r) => r.json())")


def _no_jobs(page, home):
    home.jobs.cancel(home.job_id)
    deadline = time.monotonic() + 20
    while lifecycle(page)["jobs"]:
        assert time.monotonic() < deadline, "the job did not stop"
        page.wait_for_timeout(100)


def _wait_for_quit(home):
    deadline = time.monotonic() + 20
    while not home.quits:
        assert time.monotonic() < deadline, "the launcher did not stop"
        time.sleep(0.05)


def _assert_working_page_gone(page):
    """Nothing of the old page can be seen, clicked or reached with Tab."""
    for selector in ("header.masthead", ".shell", "nav.sidenav", "#main", ".skip-link"):
        node = page.locator(selector)
        assert not node.is_visible(), selector
        assert node.evaluate("(n) => n.closest('[inert]') !== null"), selector
    focusable = page.evaluate(FOCUSABLE)
    assert all(name == "Check again" for name in focusable), focusable
    # Tabbing from the heading stays inside the final page.
    for _ in range(4):
        page.keyboard.press("Tab")
        assert page.evaluate("document.activeElement.closest('.shell, header, aside') === null")


def test_quit_without_work_shows_the_stopped_page(page, home):
    _no_jobs(page, home)
    page.get_by_role("button", name="Quit").click()
    page.get_by_role("heading", level=1, name=STOPPED).wait_for()
    _wait_for_quit(home)
    assert page.title() == "Stopped · opncafyr"
    assert page.evaluate("document.activeElement.id") == "end-title"
    assert page.get_by_text("This tab can no longer open studies or run work.").is_visible()
    assert page.get_by_text("start it from your app shortcut").is_visible()
    assert not page.get_by_role("button", name="Check again").is_visible()
    assert page.locator("#end-view").get_by_text("Research use only.").is_visible()
    _assert_working_page_gone(page)

    # No request leaves the page once it has stopped, so no stale errors appear.
    requests: list[str] = []
    page.on("request", lambda r: requests.append(r.url))
    page.evaluate("location.hash = '#/jobs'")
    page.wait_for_timeout(3500)
    assert requests == []
    assert page.title() == "Stopped · opncafyr"
    assert page.errors == []
    _axe(page)


def test_stopped_page_fits_a_narrow_window(page, home):
    _no_jobs(page, home)
    page.get_by_role("button", name="Quit").click()
    page.get_by_role("heading", level=1, name=STOPPED).wait_for()
    # 320 CSS pixels: a phone, or a desktop window zoomed to 400 %.
    page.set_viewport_size({"width": 320, "height": 640})
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    assert page.get_by_role("heading", level=1, name=STOPPED).is_visible()


def test_cancel_jobs_and_quit_says_what_was_cancelled(page, home):
    page.get_by_role("button", name="Quit").click()
    dialog = page.get_by_role("dialog", name="Jobs are still running")
    dialog.get_by_role("button", name="Cancel jobs and quit").click()
    page.get_by_role("heading", level=1, name=STOPPED).wait_for()
    assert page.locator("#end-detail").inner_text() == "1 job was cancelled."
    _wait_for_quit(home)
    assert page.errors == []


def test_keep_running_shows_finishing_work_and_can_be_undone(page, home):
    page.get_by_role("button", name="Quit").click()
    dialog = page.get_by_role("dialog", name="Jobs are still running")
    dialog.get_by_role("button", name="Keep running and quit when finished").click()
    pending = page.get_by_role("region", name="Finishing work before quitting")
    pending.wait_for()
    assert "1 job still running" in pending.get_by_role("status").inner_text()
    pending.locator("li", has_text=JOB_LABEL).wait_for()
    assert page.evaluate("document.activeElement.id") == "keep-open"
    # Not stopped yet: the working page is still there.
    assert not page.get_by_role("heading", level=1, name=STOPPED).is_visible()
    assert page.get_by_role("navigation", name="Main").is_visible()
    assert lifecycle(page)["quit_pending"] is True
    _axe(page)

    pending.get_by_role("button", name="Keep the app open").click()
    pending.wait_for(state="hidden")
    page.locator("#app-status", has_text="Quitting was cancelled. The app stays open.").wait_for()
    assert page.evaluate("document.activeElement.id") == "quit"
    assert lifecycle(page)["quit_pending"] is False
    assert home.quits == []
    assert page.errors == []


def test_finishing_work_becomes_the_stopped_page(page, home):
    page.get_by_role("button", name="Quit").click()
    dialog = page.get_by_role("dialog", name="Jobs are still running")
    dialog.get_by_role("button", name="Keep running and quit when finished").click()
    page.get_by_role("region", name="Finishing work before quitting").wait_for()
    home.jobs.cancel(home.job_id)
    # The launcher stops on its next lifecycle tick; the page sees it on its next poll.
    page.get_by_role("heading", level=1, name=STOPPED).wait_for(timeout=20000)
    assert page.locator("#end-detail").is_hidden()  # this tab asked; nothing more to say
    _wait_for_quit(home)
    _assert_working_page_gone(page)
    assert page.errors == []


def test_cancelled_quit_dialog_keeps_the_page(page, home):
    page.get_by_role("button", name="Quit").click()
    dialog = page.get_by_role("dialog", name="Jobs are still running")
    dialog.get_by_role("button", name="Don't quit").click()
    page.locator("#app-status", has_text="Quit was cancelled. The app stays open.").wait_for()
    assert page.get_by_role("navigation", name="Main").is_visible()
    assert page.locator("#end-view").is_hidden()
    assert home.quits == []


def test_a_failed_quit_request_keeps_the_page(page, home):
    page.route(
        "**/api/quit",
        lambda route: route.fulfill(
            status=500, content_type="application/json", body='{"detail": "Disk is full."}'
        ),
    )
    _no_jobs(page, home)
    page.get_by_role("button", name="Quit").click()
    page.locator("#app-status", has_text="Quit didn't go through: Disk is full.").wait_for()
    assert page.locator("#end-view").is_hidden()
    assert page.get_by_role("button", name="Quit").is_enabled()
    assert home.quits == []
    # Expected: the browser logs the 500 it was given.
    page.errors.clear()


def test_a_lost_connection_is_not_reported_as_a_quit(page, home):
    page.route("**/api/**", lambda route: route.abort())
    # The jobs list polls every few seconds; its failure starts the check.
    page.get_by_role("heading", level=1, name=LOST).wait_for(timeout=15000)
    assert page.title() == "Connection lost · opncafyr"
    assert page.evaluate("document.activeElement.id") == "end-title"
    assert not page.get_by_text(STOPPED).is_visible()
    assert page.get_by_text("Check whether it is still running").is_visible()
    _assert_working_page_gone(page)
    assert home.quits == []
    _axe(page)

    # Still unreachable: Check again says so and stays.
    retry = page.get_by_role("button", name="Check again")
    retry.click()
    page.locator("#end-retry-status", has_text="opncafyr still isn't answering.").wait_for()

    # Back again: Check again reloads into the working page.
    page.unroute("**/api/**")
    retry.click()
    page.get_by_role("button", name="Quit").wait_for()
    assert page.get_by_role("navigation", name="Main").is_visible()
    # Expected: the browser logs the refused requests.
    page.errors.clear()
