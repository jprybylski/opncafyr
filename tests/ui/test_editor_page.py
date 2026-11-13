"""The project-file editor in a real browser (#52): source, visual, preview, conflicts, Quit.

Uses the bundled report editor on a hand-made ``report/`` folder; no R is needed.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from test_home_page import _axe

from opncafyr.adapters import quartifyr as Q

pytestmark = pytest.mark.ui

PARAGRAPH = "This report was assembled by opncafyr"
SHOTS = os.environ.get("OPNCAFYR_EDITOR_SHOTS")  # a folder: save light/dark screenshots


def report_text() -> str:
    items = [Q.ReportItem("t.csv", "table", "Summary", "Drug A", "20260101T000000Z-aaaa")]
    rows = [{"run_id": "20260101T000000Z-aaaa", "analyte": "Drug A"}]
    text = Q.report_source(items, "pub-20260101T000000Z-aaaa", None, rows) + "\n"
    return text + "\n```{r}\nx <- 1\n```\n"


@pytest.fixture
def editor(page, home):
    project = home.study / "report"
    project.mkdir()
    (project / "report.qmd").write_text(report_text(), "utf-8")
    (project / "_quarto.yml").write_text(Q.QUARTO_PROJECT, "utf-8")
    page.goto(page.url.split("#")[0] + "#/tools/quartifyr/files")
    page.get_by_role("heading", level=1, name="Report source").wait_for()
    page.get_by_role("textbox", name="Source of Report").wait_for()
    return page, project / "report.qmd"


def lifecycle(page) -> dict:
    return page.evaluate("fetch('api/lifecycle').then((r) => r.json())")


def test_source_edit_and_save(editor):
    page, path = editor
    area = page.get_by_role("textbox", name="Source of Report")
    area.press("ControlOrMeta+End")
    area.type("Closing words.\n")
    page.locator("#files-dirty", has_text="Unsaved changes").wait_for()
    # The heartbeat lists the tool, so the launcher won't close by itself.
    page.wait_for_timeout(300)
    assert lifecycle(page)["unsaved"] == ["quartifyr"]
    page.keyboard.press("ControlOrMeta+s")
    page.locator("#files-status", has_text="Saved Report.").wait_for()
    assert path.read_text("utf-8") == report_text() + "Closing words.\n"
    page.wait_for_timeout(300)
    assert lifecycle(page)["unsaved"] == []
    assert page.errors == []


def test_visual_edit_changes_only_one_paragraph(editor):
    page, path = editor
    page.get_by_role("button", name="Visual").click()
    page.get_by_text("Locked: Front matter (YAML)").wait_for()
    assert page.get_by_text("Locked: Code chunk").count() == 1
    blocks = page.locator("textarea.md-prose")
    index = next(i for i in range(blocks.count()) if PARAGRAPH in blocks.nth(i).input_value())
    block = blocks.nth(index)
    block.fill("A shorter introduction.")
    page.get_by_role("button", name="Save").click()
    page.locator("#files-status", has_text="Saved Report.").wait_for()
    old = report_text()
    start = old.index(PARAGRAPH)
    end = old.index("\n\n", start)
    assert path.read_text("utf-8") == old[:start] + "A shorter introduction." + old[end:]
    # Locked syntax typed into a prose block is refused and not kept.
    block.fill("Now with {{< pagebreak >}}")
    page.get_by_text("visual mode keeps locked").wait_for()
    assert block.get_attribute("aria-invalid") == "true"
    assert page.errors == []


def test_preview_renders_prose_and_labels_the_rest(editor):
    page, _ = editor
    page.get_by_role("button", name="Preview").click()
    preview = page.locator(".md-preview")
    preview.get_by_role("heading", name="Introduction").wait_for()
    assert preview.get_by_text("Code chunk: not shown in the preview").count() == 1
    assert preview.locator("script, iframe, img").count() == 0


def test_a_conflict_keeps_both_versions(editor):
    page, path = editor
    area = page.get_by_role("textbox", name="Source of Report")
    area.press("ControlOrMeta+End")
    area.type("Mine.\n")
    path.write_text("Theirs.\n", "utf-8")
    page.get_by_role("button", name="Save").click()
    alert = page.get_by_role("alert")
    alert.get_by_text("The file changed on disk").wait_for()
    assert alert.get_by_role("textbox", name="The version on disk").input_value() == "Theirs.\n"
    assert area.input_value().endswith("Mine.\n")
    assert path.read_text("utf-8") == "Theirs.\n"
    alert.get_by_role("button", name="Keep mine (replace the disk version)").click()
    page.locator("#files-status", has_text="Saved Report.").wait_for()
    assert path.read_text("utf-8").endswith("Mine.\n")
    # Chromium logs the expected 409 itself; nothing else may fail.
    assert [e for e in page.errors if "409 (Conflict)" not in e] == []


def test_settings_form_saves_the_report_settings(editor, home):
    page, _ = editor
    page.get_by_label("Document status").select_option("final")
    page.locator("#files-status", has_text="Settings saved.").wait_for()
    saved = json.loads((home.study / "report" / "opncafyr-settings.json").read_text("utf-8"))
    assert saved == {"schema_version": 1, "status": "final", "reference_doc": "bundled"}


def test_quit_saves_editor_text_first(editor, home):
    page, path = editor
    home.jobs.cancel(home.job_id)
    area = page.get_by_role("textbox", name="Source of Report")
    area.press("ControlOrMeta+End")
    area.type("Saved on quit.\n")
    page.get_by_role("button", name="Quit").click()
    dialog = page.get_by_role("dialog", name="Unsaved changes")
    dialog.wait_for()
    dialog.get_by_role("button", name="Save and quit").click()
    page.get_by_role("heading", level=1, name="opncafyr has stopped").wait_for()
    assert path.read_text("utf-8").endswith("Saved on quit.\n")
    # The save is stated because this tab's editor confirmed it (#73).
    assert page.locator("#end-detail").inner_text() == (
        "Changes in this tab's editors were saved before quitting."
    )
    for _ in range(100):
        if home.quits:
            break
        page.wait_for_timeout(100)
    assert home.quits == ["quit"]


def test_going_back_to_save_keeps_the_page_and_the_text(editor, home):
    page, path = editor
    home.jobs.cancel(home.job_id)
    area = page.get_by_role("textbox", name="Source of Report")
    area.press("ControlOrMeta+End")
    area.type("Not yet.\n")
    page.get_by_role("button", name="Quit").click()
    dialog = page.get_by_role("dialog", name="Unsaved changes")
    dialog.get_by_role("button", name="Go back and save").click()
    note = "Quit was cancelled. Your changes are still open."
    page.locator("#app-status", has_text=note).wait_for()
    assert page.locator("#end-view").is_hidden()
    assert area.input_value().endswith("Not yet.\n")
    assert path.read_text("utf-8") == report_text()
    assert home.quits == []
    # The only console error is the refused Quit (409) the browser logs.
    assert [e for e in page.errors if "409" not in e] == []


def test_editor_page_has_no_axe_violations(editor, request):
    page, _ = editor
    _axe(page)
    page.get_by_role("button", name="Visual").click()
    page.get_by_text("Locked: Front matter (YAML)").wait_for()
    _axe(page)
    if SHOTS:
        scheme = page.evaluate("matchMedia('(prefers-color-scheme: dark)').matches")
        name = "dark" if scheme else "light"
        page.get_by_role("button", name="Source").click()
        page.screenshot(path=str(Path(SHOTS) / f"editor-{name}.png"), full_page=False)


def test_editor_assets_load_only_from_the_origin(editor, home):
    page, _ = editor
    seen = []
    page.on("request", lambda req: seen.append(req.url))
    page.reload()
    page.get_by_role("textbox", name="Source of Report").wait_for()
    page.get_by_role("button", name="Preview").click()
    assert all(url.startswith(home.url) for url in seen), seen


def test_new_fragment_from_the_page(page, home):
    (home.study / "deck").mkdir()
    page.goto(page.url.split("#")[0] + "#/tools/deckifyr/files")
    page.get_by_role("heading", level=1, name="Deck fragments").wait_for()
    name = page.get_by_label("New quarto fragment name")
    name.fill("intro")
    page.get_by_text("Saved as fragments/intro.qmd").wait_for()
    page.get_by_role("button", name="Create").click()
    page.locator("#files-status", has_text="Created fragments/intro.qmd.").wait_for()
    area = page.get_by_role("textbox", name="Source of fragments/intro.qmd")
    area.wait_for()
    assert (home.study / "deck" / "fragments" / "intro.qmd").read_text("utf-8") == (
        area.input_value()
    )
    # The same name again is refused, and nothing is overwritten.
    name.fill("intro")
    page.get_by_role("button", name="Create").click()
    page.locator("#files-status", has_text="already exists").wait_for()
    _axe(page)
    assert [e for e in page.errors if "409 (Conflict)" not in e] == []


def test_the_settings_file_opens_with_its_description_and_notes_a_bad_value(editor):
    # #64: config.yaml is offered next to the report, described, and checked on save.
    page, _ = editor
    folder = editor[1].parent / "report"
    folder.mkdir()
    config = folder / "config.yaml"
    config.write_text(Q.template().joinpath("config.yaml").read_text("utf-8"), "utf-8")
    page.reload()
    page.get_by_role("button", name="Report settings (config.yaml)").click()
    page.locator("#files-editor-heading", has_text="Report settings (config.yaml)").wait_for()
    page.get_by_text("Most reports need no change.").wait_for()
    area = page.get_by_role("textbox", name="Source of Report settings (config.yaml)")
    area.wait_for()
    assert "# Figures." in area.input_value()  # the file's own comments are shown
    area.press("ControlOrMeta+End")
    area.type("use_artifact_size: maybe\nfig_aligment: left\n")
    page.keyboard.press("ControlOrMeta+s")
    page.locator("#files-status", has_text="Saved, with notes").wait_for()
    notes = page.locator("#files-status").inner_text()
    assert "fig_aligment" in notes and "spelling" in notes
    assert config.read_text("utf-8").endswith("fig_aligment: left\n")  # saved as typed
    assert page.errors == []
