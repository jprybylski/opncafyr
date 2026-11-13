"""The Standards page in a real browser (#70, ADR 0011): preview, conflicts, install, select."""

from __future__ import annotations

import os

import pytest

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
