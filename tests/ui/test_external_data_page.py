"""The External data page in a real browser (#83): render, keyboard, axe (light, dark)."""

import pytest

pytest.importorskip("opncafyr.workflows.datafetch")

from test_home_page import _axe  # noqa: E402

pytestmark = pytest.mark.ui

SHA = "a" * 64
STATUS = {
    "workspace_open": True,
    "installed": True,
    "available": True,
    "detail": None,
    "note": "Fetching tells you which bytes you have and where they came from.",
    "config_file": ".data.yaml",
    "source_data": "source-data",
    "exists": True,
    "jobs_active": False,
    "interrupted": None,
    "reviews": [],
    "previous": {"pk": {"files": 1, "bytes": 2048}},
    "sha256": SHA,
    "has_comments": False,
    "problems": [],
    "credentials": ["TOKEN"],
    "last_check_at": "2026-11-15T10:00:00Z",
    "datasets": [
        {
            "id": "pk",
            "desc": "PK concentrations",
            "types": ["http"],
            "sources": ["data.example.org/pk.csv"],
            "target": "source-data/pk.csv",
            "policy": "fail",
            "editable": True,
            "source": {"type": "http", "url": "https://data.example.org/pk.csv?t=${TOKEN}"},
            "audit": {"id": "pk", "status": "ok", "in_config": True},
            "last_check": {"id": "pk", "status": "fail", "message": "changed upstream"},
            "problems": [],
            "fetched_at": "2026-11-14T09:00:00Z",
            "matches_fetched": True,
            "files": ["source-data/pk.csv"],
        },
        {
            "id": "share",
            "desc": "",
            "types": ["file"],
            "sources": ["/mnt/share/dose.csv"],
            "target": "source-data/dose.csv",
            "policy": "log",
            "editable": True,
            "source": {"type": "file", "path": "/mnt/share/dose.csv"},
            "audit": {"id": "share", "status": "pending", "in_config": True},
            "last_check": None,
            "problems": [],
            "approval": {"path": "/mnt/share/dose.csv", "approved": False},
        },
    ],
    "orphaned": [],
}
TYPES = {
    "types": [
        {"type": t, "description": f"{t} source", "fields": []} for t in ("file", "git", "http")
    ],
    "policies": ["fail", "update", "log"],
}


@pytest.fixture
def external(page):
    page.route("**/api/datafetch/status", lambda route: route.fulfill(json=STATUS))
    page.route("**/api/datafetch/types", lambda route: route.fulfill(json=TYPES))
    page.route("**/api/datafetch/validate", lambda route: route.fulfill(json={"problems": []}))
    page.goto(page.url.split("#")[0] + "#/external-data")
    page.get_by_role("heading", level=1, name="External data").wait_for()
    page.get_by_role("button", name="Check for updates").wait_for()
    return page


def test_the_page_lists_datasets_and_their_state(external):
    page = external
    table = page.get_by_role("table", name="Datasets in the external data settings")
    assert table.get_by_role("rowheader").all_inner_texts()[0].startswith("pk")
    assert table.locator(".pill", has_text="Changed upstream").is_visible()
    assert table.locator(".pill", has_text="Approval needed").is_visible()
    assert page.get_by_label("${TOKEN}").get_attribute("type") == "password"
    assert page.get_by_text("which bytes you have", exact=False).is_visible()
    assert page.locator('[data-target="datafetch.check"]').count() == 1
    assert not page.errors


def test_the_dataset_form_opens_from_the_keyboard(external):
    page = external
    edit = page.get_by_role("button", name="Edit pk")
    edit.focus()
    page.keyboard.press("Enter")
    page.get_by_role("heading", name="Edit pk").wait_for()
    form = page.locator("#df-form")
    field = form.get_by_role("textbox", name="Id", exact=True)
    assert page.evaluate("document.activeElement.id") == field.get_attribute("id")
    assert form.get_by_label("URL").input_value().startswith("https://data.example.org/")
    form.get_by_role("button", name="Cancel").click()
    assert not page.get_by_role("heading", name="Edit pk").is_visible()


def test_a_confirmation_lists_the_paths_and_never_uses_a_browser_dialog(external):
    page = external
    page.on("dialog", lambda d: (_ for _ in ()).throw(AssertionError("browser dialog")))
    page.get_by_role("button", name="Delete files of pk").click()
    box = page.get_by_role("group", name="Delete files")
    assert box.get_by_text("source-data/pk.csv").is_visible()
    box.get_by_role("button", name="Cancel").click()


def test_external_data_page_has_no_axe_violations(external):
    _axe(external)
    external.get_by_role("button", name="Add a dataset").click()
    external.get_by_role("heading", name="Add a dataset").wait_for()
    _axe(external)
