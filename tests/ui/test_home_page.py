"""The home page in a real browser (#49, #50): rendering, origin, keyboard, axe, theme."""

import time
from pathlib import Path

import pytest
from home_seed import BROKEN_RUN_ID, JOB_LABEL, RUN_ID

from artifact_docs import PUB_ID
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
    """The state word of a row's status pill (the symbol is aria-hidden)."""
    return row.locator(".state").inner_text().split(maxsplit=1)[1]


def test_home_renders_with_stub_adapters(page):
    assert page.get_by_text("Research use only.").is_visible()
    tiles = page.locator("#tools")
    for tool in load_descriptors().values():
        assert tiles.get_by_role("heading", name=tool.display_name or tool.id).is_visible()
    assert page.get_by_role("link", name="Open toy").get_attribute("href") == "#/apps/toy"

    runs = page.locator("#runs-body")
    assert _state(runs.locator("tr", has_text=RUN_ID)) == "Completed"
    assert _state(runs.locator("tr", has_text=BROKEN_RUN_ID)) == "Incomplete"
    assert page.locator("#pubs-body").get_by_text(PUB_ID).is_visible()

    assert _state(page.locator("#home-jobs li", has_text=JOB_LABEL)) == "Running"
    assert page.locator("#jobs-live").inner_text() == "1 job is running or waiting."
    assert page.locator("#live-jobs").inner_text() == "1 running"
    assert page.locator("#study-chip").get_by_text("Study A", exact=True).is_visible()
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
    assert "Quit" in expected
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
    page.locator("#home-jobs li", has_text=JOB_LABEL).locator(
        ".state", has_text="Cancelled"
    ).wait_for()
    assert home.jobs.get(home.job_id)["state"] == "cancelled"
    assert page.errors == []


def test_quit_dialog_can_be_dismissed(page, home):
    page.get_by_role("button", name="Quit").click()
    dialog = page.get_by_role("dialog", name="Jobs are still running")
    dialog.wait_for()
    page.keyboard.press("Escape")
    dialog.wait_for(state="hidden")
    assert home.quits == []
    assert page.get_by_role("button", name="Quit").is_enabled()


def _axe(page):
    axe = pytest.importorskip("axe_playwright_python.sync_playwright")
    results = axe.Axe().run(page)
    assert results.violations_count == 0, results.generate_report()


def test_axe_finds_no_violations(page):
    _axe(page)


@pytest.mark.parametrize(
    ("view", "heading"), [("data", "Data"), ("jobs", "Jobs"), ("settings", "Settings")]
)
def test_views_from_the_sidebar(page, view, heading):
    nav = page.get_by_role("navigation", name="Main")
    nav.get_by_role("link", name=heading, exact=False).first.click()
    # The view changes on "hashchange", a task after the click.
    page.get_by_role("heading", level=1, name=heading).wait_for()
    assert page.evaluate("document.activeElement.textContent") == heading
    assert not page.locator("#view-home").is_visible()
    current = nav.locator('[aria-current="page"]')
    assert current.get_attribute("data-view") == view
    assert page.title() == f"{heading} · opncafyr"
    assert page.get_by_text("Research use only.").is_visible()
    if view == "jobs":
        assert _state(page.locator("#jobs-list li", has_text=JOB_LABEL)) == "Running"
    _axe(page)
    page.go_back()
    page.locator("#view-home").wait_for()


def test_skip_link_keeps_the_view(page):
    page.goto(page.url.split("#")[0] + "#/settings")
    page.reload()  # a fresh load, so the first Tab reaches the skip link
    page.get_by_role("heading", level=1, name="Settings").wait_for()
    page.keyboard.press("Tab")
    page.keyboard.press("Enter")
    assert page.evaluate("document.activeElement.id") == "main"
    assert page.get_by_role("heading", level=1, name="Settings").is_visible()


def test_support_bundle_is_previewed_then_saved(page, home):
    page.goto(page.url.split("#")[0] + "#/settings")
    page.get_by_role("heading", level=1, name="Settings").wait_for()
    page.get_by_role("button", name="Create support bundle…").click()
    dialog = page.locator("#support-dialog")
    dialog.wait_for()
    assert dialog.locator("summary", has_text="jobs.json").is_visible()
    dialog.locator("summary", has_text="README.txt").click()
    assert "Research use only" in dialog.locator("pre").first.inner_text()
    _axe(page)
    dialog.get_by_role("button", name="Save on this computer").click()
    page.locator("#support-status", has_text="Saved support-").wait_for()
    link = page.locator("#support-saved a").first
    link.wait_for()
    saved = list((home.app_root / "logs" / "support").glob("support-*.zip"))
    assert [p.name for p in saved] == [link.inner_text()]
    assert page.errors == []


GUIDE = Path(__file__).resolve().parents[2] / "docs" / "user-guide"


@pytest.mark.skipif(not (GUIDE / "index.md").is_file(), reason="no guide yet")
def test_guide_renders_with_the_notice_and_chapters(page):
    page.get_by_role("link", name="Read the guide").click()
    page.locator("#guide-article blockquote").wait_for()
    assert page.locator("#guide-heading").inner_text() == "opncafyr user guide"
    notice = page.locator("#guide-article blockquote").first.inner_text()
    assert notice.startswith("Research use only.")
    chapters = page.locator("#guide-chapters a")
    assert chapters.count() > 5
    # Follow a chapter link from the contents; the nav marks it.
    second = chapters.nth(1)
    name = second.inner_text()
    second.click()
    page.locator("#guide-chapters a[aria-current='page']", has_text=name).wait_for()
    assert page.evaluate("location.hash").startswith("#/guide/")
    # Every image that loaded has a size (none is broken).
    page.goto(page.url.split("#")[0] + "#/guide/walkthrough")
    page.locator("#guide-heading", has_text="walkthrough").or_(
        page.locator("#guide-heading", has_text="Walkthrough")
    ).first.wait_for()
    for img in page.locator("#guide-article img").all():
        img.scroll_into_view_if_needed()
        assert page.evaluate("(i) => i.complete && i.naturalWidth > 0", img.element_handle())
    _axe(page)


def test_qc_chapter_renders_its_tables_and_scripts(page):
    """#71: the review chapter is usable offline, with its record template and scripts."""
    page.goto(page.url.split("#")[0] + "#/guide/qc")
    page.locator("#guide-heading", has_text="Reviewing a run (QC)").wait_for()
    article = page.locator("#guide-article")
    assert article.locator("table").count() == 2
    code = article.locator("pre code")
    assert code.count() == 2
    assert "library(PKNCA)" in code.nth(1).inner_text()
    assert article.locator("blockquote").first.inner_text().startswith("Research use only.")
    _axe(page)
    assert page.errors == []


BG = "getComputedStyle(document.body).backgroundColor"
LIGHT_BG, DARK_BG = "rgb(242, 245, 244)", "rgb(12, 20, 19)"


def test_unset_theme_follows_the_os(page):
    assert page.evaluate("document.documentElement.dataset.theme") == "system"
    dark = page.evaluate("matchMedia('(prefers-color-scheme: dark)').matches")
    assert page.evaluate(BG) == (DARK_BG if dark else LIGHT_BG)
    assert page.locator("#theme-toggle").inner_text() == "Theme: System"


def test_theme_toggle_overrides_the_os_and_persists(page, home):
    toggle = page.locator("#theme-toggle")
    for word, bg in (("Light", LIGHT_BG), ("Dark", DARK_BG)):
        with page.expect_response("**/api/settings") as resp:
            toggle.click()
        assert resp.value.ok
        assert toggle.inner_text() == f"Theme: {word}"
        assert page.evaluate(BG) == bg
        # A new launch link renders the saved theme server-side, before any script.
        page.goto(home.open_url())
        assert page.evaluate("document.documentElement.dataset.theme") == word.lower()
        assert page.evaluate(BG) == bg
        page.locator("#theme-toggle", has_text=word).wait_for()
    _axe(page)


def test_component_reference_page(page, home):
    page.goto(home.url + "static/components.html")
    page.get_by_role("heading", level=1, name="Component reference").wait_for()
    _axe(page)
    page.get_by_role("button", name="Dark").click()
    assert page.evaluate(BG) == DARK_BG
    _axe(page)
    page.get_by_role("button", name="Open the example dialog").click()
    dialog = page.get_by_role("dialog", name="Discard the cleaning changes?")
    dialog.wait_for()
    page.keyboard.press("Escape")
    dialog.wait_for(state="hidden")
    assert page.errors == []


def test_a_tool_opens_inside_the_launcher(page):
    page.get_by_role("link", name="Open toy").click()
    page.get_by_role("heading", level=1, name="toy").wait_for()
    assert page.evaluate("document.activeElement.id") == "tool-heading"
    assert page.title() == "toy · opncafyr"
    assert page.evaluate("document.body.classList.contains('in-tool')")
    # The launcher chrome stays: notice, jobs indicator, Quit and the (folded) sidebar.
    assert page.get_by_text("Research use only.").is_visible()
    assert page.locator("#live-jobs").inner_text() == "1 running"
    assert page.get_by_role("button", name="Quit").is_visible()
    nav = page.get_by_role("navigation", name="Main")
    assert nav.locator('[aria-current="page"]').get_attribute("data-app") == "toy"
    assert page.locator(".app-state").inner_text().split(maxsplit=1)[1] == "Ready"
    new_tab = page.get_by_role("link", name="Open in new tab")
    assert new_tab.get_attribute("href") == "apps/toy/"
    assert new_tab.get_attribute("target") == "_blank"

    frame = page.frame_locator('iframe[title="toy"]')
    frame.get_by_text("toy", exact=True).wait_for()
    # The frame stays loaded across views, so work in a tool survives a visit to Home.
    page.evaluate("document.querySelector('iframe[title=toy]').contentWindow.kept = 1")
    nav.get_by_role("link", name="Home").click()
    page.get_by_role("heading", level=1, name="Home").wait_for()
    assert not page.evaluate("document.body.classList.contains('in-tool')")
    nav.locator('[data-app="toy"]').click()
    page.get_by_role("heading", level=1, name="toy").wait_for()
    assert page.locator("iframe.tool-frame").count() == 1
    assert page.evaluate("document.querySelector('iframe[title=toy]').contentWindow.kept") == 1
    _axe(page)
    assert page.errors == []


FRAME = "document.querySelector('iframe[title=toy]')"


def test_a_tool_out_of_sight_says_it_is_hidden(page):
    # #67: a kept frame tells its page it's hidden, as a background tab does, so a tool's
    # polling (version history's Git status every 2 s) stops when another view is shown.
    page.get_by_role("link", name="Open toy").click()
    page.frame_locator('iframe[title="toy"]').get_by_text("toy", exact=True).wait_for()
    assert page.evaluate(f"{FRAME}.contentDocument.visibilityState") == "visible"
    page.evaluate(
        f"""(() => {{
          const doc = {FRAME}.contentDocument;
          const seen = (doc.defaultView.seen = []);
          doc.addEventListener("visibilitychange", () => seen.push(doc.visibilityState));
        }})()"""
    )
    nav = page.get_by_role("navigation", name="Main")
    nav.get_by_role("link", name="Home").click()
    page.get_by_role("heading", level=1, name="Home").wait_for()
    assert page.evaluate(f"{FRAME}.contentDocument.visibilityState") == "hidden"
    assert page.evaluate(f"{FRAME}.contentDocument.hidden") is True
    nav.locator('[data-app="toy"]').click()
    page.get_by_role("heading", level=1, name="toy").wait_for()
    assert page.evaluate(f"{FRAME}.contentDocument.visibilityState") == "visible"
    assert page.evaluate(f"{FRAME}.contentWindow.seen") == ["hidden", "visible"]
    # A reload while out of sight starts hidden too.
    nav.get_by_role("link", name="Home").click()
    page.get_by_role("heading", level=1, name="Home").wait_for()
    page.evaluate(f"{FRAME}.contentWindow.location.reload()")
    reloaded = (  # a new document (no "seen"), loaded, that says it is hidden
        f"!{FRAME}.contentWindow.seen && {FRAME}.contentDocument.readyState === 'complete'"
        f" && {FRAME}.contentDocument.visibilityState === 'hidden'"
    )
    deadline = time.monotonic() + 10
    while not page.evaluate(reloaded):
        assert time.monotonic() < deadline, "the reloaded frame never said it was hidden"
        time.sleep(0.05)
    assert page.errors == []


def test_a_tool_link_works_on_a_fresh_load(page, home):
    page.goto(home.url + "#/apps/toy")
    page.reload()
    page.frame_locator('iframe[title="toy"]').get_by_text("toy", exact=True).wait_for()
    page.goto(home.url + "#/apps/nope")
    page.get_by_text('No installed tool has a web app called "nope".').wait_for()
    assert page.get_by_role("button", name="Reload").is_disabled()
    assert page.errors == []


def test_preview_and_import_a_dataset(page):
    page.goto(page.url.split("#")[0] + "#/data")
    page.get_by_role("heading", level=1, name="Data").wait_for()
    path = "source-data/two_analyte.csv"
    row = page.locator("#datasets-body tr", has_text=path)
    row.wait_for()
    assert "168" in row.inner_text()
    row.get_by_role("button", name=f"Preview {path}").click()
    page.get_by_role("heading", name=f"Preview: {path}").wait_for()
    values = page.get_by_role("table", name="Values of PCTESTCD").inner_text()
    for value in ('"PARENT"', '"METAB"', '"METAB "'):
        assert value in values
    assert page.get_by_role("table", name="Columns, types and missing values").is_visible()
    assert page.locator(".rows-table tbody tr").count() == 20
    _axe(page)
    page.get_by_role("button", name="Import", exact=True).click()
    page.locator("#preview-status").get_by_text("Imported as source").wait_for()
    row.locator(".state", has_text="Recorded").wait_for()
    assert page.errors == []
