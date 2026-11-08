"""The Version history page in a real browser (#56)."""

import pytest

pytestmark = pytest.mark.ui

AVAILABLE = {
    "workspace_open": True,
    "installed": True,
    "available": True,
    "detail": None,
    "running": False,
    "remote": False,
    "route": "/apps/gitneighbr/",
}


def test_history_page_offers_one_set_of_buttons(page):
    # The stub runtime has no gitneighbr, so answer as an install that has it.
    page.route("**/api/nca/history", lambda route: route.fulfill(json=AVAILABLE))
    page.goto(page.url.split("#")[0] + "#/history")
    page.reload()  # loading the page and showing it both refresh the card
    opener = page.get_by_role("button", name="Open version history")
    opener.first.wait_for()
    page.wait_for_load_state("networkidle")
    assert opener.count() == 1
    assert page.get_by_label("Also allow sending to, and getting updates from").is_visible()
    assert not page.get_by_label("Also allow sending to, and getting updates from").is_checked()
    assert page.get_by_text("not a run, a publication or a build", exact=False).is_visible()
