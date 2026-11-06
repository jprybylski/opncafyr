"""The home page in a real browser (#49): rendering, origin, keyboard, axe."""

import pytest
from home_seed import BROKEN_RUN_ID, JOB_LABEL, RUN_ID

from opncafyr.core.registry import load_descriptors

pytestmark = pytest.mark.ui

FOCUSABLE = """() => {
  const sel = 'a[href], button, select, input, textarea, summary, [tabindex]';
  const els = [...document.querySelectorAll(sel)].filter((e) =>
    e.tabIndex >= 0 && !e.disabled && e.checkVisibility() && !e.closest('dialog:not([open])'));
  els.forEach((e, i) => { e.dataset.walk = String(i); });
  return els.map((e) => e.getAttribute('aria-label') || e.textContent.trim() || e.id);
}"""


def _state(row):
    """The state word of a table row's badge (the symbol is aria-hidden)."""
    return row.locator(".state").inner_text().split(maxsplit=1)[1]


def test_home_renders_with_stub_adapters(page):
    assert page.get_by_text("Research use only.").is_visible()
    tiles = page.locator("#tools")
    for tool in load_descriptors().values():
        assert tiles.get_by_role("heading", name=tool.display_name or tool.id).is_visible()
    assert page.get_by_role("link", name="Open toy").get_attribute("href") == "apps/toy/"

    runs = page.locator("#runs-body")
    assert _state(runs.locator("tr", has_text=RUN_ID)) == "Completed"
    assert _state(runs.locator("tr", has_text=BROKEN_RUN_ID)) == "Incomplete"
    assert page.locator("#pubs-body").get_by_text("pub-1").is_visible()

    assert _state(page.locator("#jobs-body tr", has_text=JOB_LABEL)) == "Running"
    assert page.locator("#jobs-live").inner_text() == "1 job is running or waiting."
    assert page.errors == []


def test_no_request_leaves_the_origin(page, home):
    seen = []
    page.on("request", lambda req: seen.append(req.url))
    page.reload()
    page.get_by_role("button", name=f"Cancel {JOB_LABEL}").wait_for()
    page.wait_for_timeout(3500)  # at least one job poll
    assert seen
    assert all(url.startswith(home.url) for url in seen), seen
    assert page.errors == []


def test_tab_walk_reaches_every_control_in_order(page):
    expected = page.evaluate(FOCUSABLE)
    assert "Skip to main content" in expected
    assert "Quit application" in expected
    assert f"Cancel {JOB_LABEL}" in expected
    order = []
    for _ in expected:
        page.keyboard.press("Tab")
        order.append(page.evaluate("document.activeElement.dataset.walk ?? null"))
    assert order == [str(i) for i in range(len(expected))], expected


def test_skip_link_moves_focus_to_main(page):
    page.keyboard.press("Tab")
    page.keyboard.press("Enter")
    assert page.evaluate("document.activeElement.id") == "main"


def test_cancel_a_job_from_the_keyboard(page, home):
    page.get_by_role("button", name=f"Cancel {JOB_LABEL}").focus()
    page.keyboard.press("Enter")
    page.locator("#jobs-body tr", has_text=JOB_LABEL).locator(
        ".state", has_text="Cancelled"
    ).wait_for()
    assert home.jobs.get(home.job_id)["state"] == "cancelled"
    assert page.errors == []


def test_quit_dialog_can_be_dismissed(page, home):
    page.get_by_role("button", name="Quit application").click()
    dialog = page.get_by_role("dialog", name="Jobs are still running")
    dialog.wait_for()
    page.keyboard.press("Escape")
    dialog.wait_for(state="hidden")
    assert home.quits == []
    assert page.get_by_role("button", name="Quit application").is_enabled()


def test_axe_finds_no_violations(page):
    axe = pytest.importorskip("axe_playwright_python.sync_playwright")
    results = axe.Axe().run(page)
    assert results.violations_count == 0, results.generate_report()
