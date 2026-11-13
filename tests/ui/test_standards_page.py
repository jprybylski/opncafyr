"""The Standards page in a real browser (#70, ADR 0011): preview, conflicts, install, select."""

from __future__ import annotations

import os
import re

import pytest

from artifact_docs import PUB_ID
from opncafyr.workflows.nca.publish import projects
from standards_helpers import PROFILE, mixed_bundle

pytestmark = pytest.mark.ui
SHOTS = os.environ.get("OPNCAFYR_UI_SHOTS")  # a folder to save screenshots in, when set


@pytest.fixture
def standards(page):
    page.goto(page.url.split("#")[0] + "#/standards")
    page.get_by_role("heading", level=1, name="Standards").wait_for()
    page.get_by_role("button", name="Check and preview").wait_for()
    return page


def shot(page, name):
    if SHOTS:
        theme = page.evaluate("matchMedia('(prefers-color-scheme: dark)').matches")
        page.screenshot(path=f"{SHOTS}/{name}-{'dark' if theme else 'light'}.png", full_page=True)


def test_preview_conflict_install_and_select(standards, home, tmp_path):
    page = standards
    src = mixed_bundle(tmp_path / "house-standards")
    # The study already has its own profile of that name.
    (home.study / "profiles").mkdir()
    (home.study / "profiles" / "dose-escalation.yaml").write_bytes(PROFILE + b"# ours\n")

    # The template choices come from the app's types; Git is off without the pinned Git.
    page.get_by_label("A deck template in deckifyr's own layout").wait_for()
    assert page.get_by_label("A Git repository (GitHub or GitHub Enterprise)").is_disabled()

    page.get_by_role("textbox", name="Folder or archive").fill(str(src))
    page.get_by_role("button", name="Check and preview").click()
    page.get_by_role("heading", name="House standards (house 1.0.0)").wait_for()
    review = page.locator("#standards-review")
    review.get_by_text("A file with this name is already there").wait_for()
    install = page.get_by_role("button", name="Install")
    assert install.is_disabled()  # a conflict needs a choice; none is made for the user
    review.get_by_text("Show the differences").click()
    review.locator(".std-diff").get_by_text("-# ours").wait_for()
    shot(page, "standards-review")

    review.get_by_label(
        "Keep mine (don't install the bundle's profiles/dose-escalation.yaml)"
    ).check()
    review.get_by_label("Use it for new reports").check()
    assert install.is_enabled()
    install.click()
    page.locator("#standards-import-status").get_by_text("Installed house/1.0.0-").wait_for()
    assert (home.study / "profiles/dose-escalation.yaml").read_bytes() == PROFILE + b"# ours\n"
    assert (home.study / "tutorials/first-analysis.yaml").is_file()

    used = page.locator("#standards-used")
    used.get_by_text("house-report from house/1.0.0-").wait_for()
    page.locator("#standards-list").get_by_role("button", name="Use for new decks").click()
    used.get_by_text("house-deck from house/1.0.0-").wait_for()
    shot(page, "standards-imported")
    used.get_by_role("button", name="Use the bundled one").first.click()
    used.get_by_text("Report template: the bundled template.").wait_for()
    assert page.errors == []


def test_a_broken_bundle_shows_its_problems_and_cannot_install(standards, tmp_path):
    page = standards
    src = mixed_bundle(tmp_path / "broken", tamper={"tutorials/first-analysis.yaml": b"x: 1\n"})
    page.get_by_role("textbox", name="Folder or archive").fill(str(src))
    page.get_by_role("button", name="Check and preview").click()
    page.get_by_text("Nothing can be installed until these are fixed").wait_for()
    page.get_by_text("doesn't match its SHA-256").wait_for()
    assert not page.get_by_role("button", name="Install").is_visible()
    assert page.errors == []


# --- "a report or deck already set up keeps its template" (said only when it applies) -------

KEEPS = "can't be changed"


def set_up_report(study):
    """A report project as setup leaves one, from the bundled template."""
    (study / "report").mkdir()
    projects.write_binding(study, projects.binding("report", "report", PUB_ID, "0" * 64, {}))


def install_from_page(page, src, select=()):
    page.get_by_role("textbox", name="Folder or archive").fill(str(src))
    page.get_by_role("button", name="Check and preview").click()
    review = page.locator("#standards-review")
    review.get_by_role("heading", name="House standards (house 1.0.0)").wait_for()
    for name in select:
        review.get_by_label(name).check()
    page.get_by_role("button", name="Install").click()
    page.locator("#standards-import-status").get_by_text("Installed house/1.0.0-").wait_for()
    return page.locator("#standards-import-status").inner_text()


def test_no_note_without_an_existing_project(standards, tmp_path):
    page = standards
    status = install_from_page(page, mixed_bundle(tmp_path / "src"), ["Use it for new reports"])
    assert KEEPS not in status
    page.locator("#standards-used").get_by_text("house-report from house/1.0.0-").wait_for()
    page.locator("#standards-list").get_by_role("button", name="Use for new decks").click()
    page.locator("#standards-list-status").get_by_text("Deck template: chosen").wait_for()
    assert KEEPS not in page.locator("#standards-list-status").inner_text()
    assert page.locator(".std-keeps").count() == 0
    assert page.errors == []


def test_an_existing_report_is_named_wherever_a_report_template_is_chosen(
    standards, home, tmp_path
):
    page = standards
    set_up_report(home.study)
    page.get_by_role("button", name="Check again").click()
    page.get_by_role("textbox", name="Folder or archive").fill(str(mixed_bundle(tmp_path / "src")))
    page.get_by_role("button", name="Check and preview").click()
    review = page.locator("#standards-review")
    review.get_by_role("heading", name="House standards (house 1.0.0)").wait_for()
    # In the review: beside "Use it for new reports", described by it; not beside decks.
    note = review.locator(".std-keeps")
    assert note.count() == 1
    text = note.inner_text()
    assert "report (report/) was set up from the bundled template" in text and KEEPS in text
    box = review.get_by_label("Use it for new reports")
    assert box.get_attribute("aria-describedby") == note.get_attribute("id")
    box.check()
    page.get_by_role("button", name="Install").click()
    status = page.locator("#standards-import-status")
    status.get_by_text("Installed house/1.0.0-").wait_for()
    assert KEEPS in status.inner_text()
    # In the templates card: for the report only.
    used = page.locator("#standards-used")
    used.get_by_text("house-report from house/1.0.0-").wait_for()
    assert used.locator(".std-keeps").count() == 1
    assert "report (report/)" in used.locator(".std-keeps").inner_text()
    # Choosing again (here: back to the bundled one) says it too; choosing a deck doesn't.
    used.get_by_role("button", name="Use the bundled one").click()
    page.locator("#standards-list-status").get_by_text(
        "Report template: back to the bundled"
    ).wait_for()
    assert KEEPS in page.locator("#standards-list-status").inner_text()
    page.locator("#standards-list").get_by_role("button", name="Use for new decks").click()
    page.locator("#standards-list-status").get_by_text("Deck template: chosen").wait_for()
    assert KEEPS not in page.locator("#standards-list-status").inner_text()
    shot(page, "standards-existing-report")
    assert page.errors == []


def report_card(page):
    page.goto(page.url.split("#")[0] + "#/publish")
    page.get_by_role("heading", level=1, name="Publish").wait_for()
    card = page.locator("#nca-report")
    card.get_by_role("button", name=re.compile("Set up report|Use this publication")).wait_for()
    return card


def test_the_report_card_says_which_template_setup_uses(standards, home, tmp_path):
    page = standards
    card = report_card(page)
    assert card.locator(".template-note").count() == 0  # bundled, nothing chosen: no note
    page.goto(page.url.split("#")[0] + "#/standards")
    install_from_page(page, mixed_bundle(tmp_path / "src"), ["Use it for new reports"])
    card = report_card(page)
    card.get_by_text("Set up report will use house-report from house 1.0.0").wait_for()
    assert KEEPS not in card.inner_text()
    assert page.errors == []


def test_the_report_card_says_an_existing_report_keeps_its_template(standards, home, tmp_path):
    page = standards
    set_up_report(home.study)
    card = report_card(page)
    assert card.locator(".template-note").count() == 0  # bundled, nothing else chosen
    page.goto(page.url.split("#")[0] + "#/standards")
    install_from_page(page, mixed_bundle(tmp_path / "src"), ["Use it for new reports"])
    card = report_card(page)
    note = card.locator(".template-note")
    note.get_by_text("Standards now chooses house-report from house 1.0.0").wait_for()
    text = note.inner_text()
    assert "keeps the bundled template" in text and KEEPS in text
    assert "Use this publication doesn't change it" in text
    shot(page, "report-card-keeps")
    assert page.errors == []


def test_standards_page_axe_with_the_note(standards, home, tmp_path):
    from test_home_page import _axe

    page = standards
    set_up_report(home.study)
    install_from_page(page, mixed_bundle(tmp_path / "src"), ["Use it for new reports"])
    page.locator("#standards-used .std-keeps").wait_for()
    _axe(page)
