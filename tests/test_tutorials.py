"""Tutorials (#69, ADR 0010): format, catalog checks, copying and progress."""

from __future__ import annotations

import json
import re
from importlib import resources
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from nca_helpers import make_study
from opncafyr.core.app import create_app
from opncafyr.core.guidance import build_catalog
from opncafyr.core.manifest import load_schema, validate
from opncafyr.core.registry import load_workflows
from opncafyr.core.security import SESSION_HEADER
from opncafyr.core.tutorials import examine
from opncafyr.core.workspace import RecentWorkspaces

CATALOG = build_catalog(load_workflows())
EXAMPLE = "example/nca/first-analysis.yaml"
STATIC = Path(str(resources.files("opncafyr.web").joinpath("static")))
SECRET = "SUBJ-0042"

SMALL = """\
schema_version: "1.0"
id: short
version: "1"
title: Short tour
steps:
  - id: one
    title: Open a folder
    view: home
    target: shell.workspace.pick
    wait_for: shell.workspace.open
    text: Choose a folder.
  - id: two
    title: Map
    view: analysis
    target: nca.mapping.card
    text: Map the columns.
"""


@pytest.fixture
def api(security, tmp_path):
    study = make_study(tmp_path)
    app = create_app(
        security,
        picker=lambda prompt, start: study,
        recent=RecentWorkspaces(tmp_path / "state" / "recent.json"),
        tutorial_progress=tmp_path / "state" / "tutorial-progress.json",
    )
    with TestClient(
        app,
        base_url=f"http://127.0.0.1:{security.port}",
        cookies={security.cookie_name: security.secret},
    ) as client:
        client.study = study
        client.progress_file = tmp_path / "state" / "tutorial-progress.json"
        client.headers[SESSION_HEADER] = security.secret
        yield client


def open_study(api):
    folder = api.post("/api/workspace/pick").json()["folder"]
    api.post("/api/workspace/init", json={"path": folder["path"]}).raise_for_status()


def problems_of(text):
    return examine(text.encode(), "t.yaml", CATALOG)["problems"]


# --- the format ----------------------------------------------------------------------------


def test_the_shipped_tutorial_is_valid():
    found = examine(
        resources.files("opncafyr.workflows.nca")
        .joinpath("tutorials/first-analysis.yaml")
        .read_bytes(),
        "first-analysis.yaml",
        CATALOG,
    )
    assert found["problems"] == []
    steps = found["tutorial"]["steps"]
    assert [s["view"] for s in steps if "view" in s][:2] == ["home", "data"]
    assert {"nca.analyte.value", "nca.runs.card", "nca.curator.card"} <= {
        s.get("target") for s in steps
    }


@pytest.mark.parametrize(
    ("edit", "expected"),
    [
        (lambda t: t.replace("view: analysis", "view: https://example.org"), "fails 'pattern'"),
        (lambda t: t.replace("view: analysis", "view: nowhere"), "names no view"),
        (lambda t: t.replace("target: nca.mapping.card", "target: nca.nope"), "names no target"),
        (
            lambda t: t.replace("wait_for: shell.workspace.open", "wait_for: shell.never"),
            "names no condition",
        ),
        (lambda t: t.replace("view: analysis", "view: publish"), "another view"),
        (lambda t: t.replace("id: two", "id: one"), "same id"),
        (
            lambda t: t.replace(
                "    text: Choose a folder.\n", "    text: Choose a folder.\n    script: alert(1)\n"
            ),
            "additionalProperties",
        ),
        (
            lambda t: t.replace(
                "    text: Choose a folder.\n",
                f"    text: Choose a folder.\n    html: <b>{SECRET}</b>\n",
            ),
            "additionalProperties",
        ),
        (
            lambda t: t.replace(
                "    text: Choose a folder.\n",
                "    text: Choose a folder.\n    endpoint: api/nca/runs\n",
            ),
            "additionalProperties",
        ),
    ],
)
def test_a_tutorial_can_only_name_what_the_app_has(edit, expected):
    problems = problems_of(edit(SMALL))
    assert problems and any(expected in p for p in problems), problems
    assert not any(SECRET in p for p in problems)


def test_the_schema_has_no_way_to_act():
    schema = load_schema("tutorial-v1")
    step = schema["properties"]["steps"]["items"]
    assert set(step["properties"]) == {"id", "title", "text", "view", "target", "wait_for"}
    assert step["additionalProperties"] is False and schema["additionalProperties"] is False


# --- the API -------------------------------------------------------------------------------


def test_list_copy_and_read(api):
    listed = api.get("/api/tutorials").json()
    assert [t["key"] for t in listed["examples"]] == [EXAMPLE]
    assert listed["study"] == [] and listed["workspace_open"] is False
    r = api.post("/api/tutorials/copy", json={"key": EXAMPLE})
    assert r.status_code == 409  # no study folder yet

    open_study(api)
    r = api.post("/api/tutorials/copy", json={"key": EXAMPLE})
    assert r.status_code == 200 and r.json()["path"] == "tutorials/first-analysis.yaml"
    assert api.post("/api/tutorials/copy", json={"key": EXAMPLE}).status_code == 409
    assert (
        api.post("/api/tutorials/copy", json={"key": "study/first-analysis.yaml"}).status_code
        == 404
    )

    (api.study / "tutorials" / "short.yaml").write_text(SMALL, "utf-8")
    (api.study / "tutorials" / "broken.yaml").write_text("steps: [", "utf-8")
    listed = api.get("/api/tutorials").json()["study"]
    by_key = {t["key"]: t for t in listed}
    assert set(by_key) == {"study/broken.yaml", "study/first-analysis.yaml", "study/short.yaml"}
    assert by_key["study/broken.yaml"]["problems"] and not by_key["study/short.yaml"]["problems"]

    one = api.get("/api/tutorials/file", params={"key": "study/short.yaml"}).json()
    assert [s["id"] for s in one["tutorial"]["steps"]] == ["one", "two"]
    for bad in ("study/../x.yaml", "/etc/passwd", "example/nca/../../x.yaml", "study/.x.yaml"):
        assert api.get("/api/tutorials/file", params={"key": bad}).status_code == 422


def test_progress_survives_and_holds_no_paths_or_data(api):
    open_study(api)
    body = {"key": EXAMPLE, "step": "mapping", "paused": True, "active": True}
    r = api.put("/api/tutorials/progress", json=body)
    assert r.status_code == 200
    doc = json.loads(api.progress_file.read_text("utf-8"))
    validate(doc, "tutorial-progress-v1")
    assert doc["active"] == EXAMPLE and doc["tutorials"][EXAMPLE]["step"] == "mapping"
    text = api.progress_file.read_text("utf-8")
    assert str(api.study) not in text and "Study" not in text

    assert api.get("/api/tutorials").json()["progress"]["tutorials"][EXAMPLE]["paused"] is True
    r = api.put("/api/tutorials/progress", json={**body, "step": "no-such-step"})
    assert r.status_code == 404
    r = api.put("/api/tutorials/progress", json={**body, "active": False, "paused": False})
    assert r.json()["active"] is None

    assert api.delete("/api/tutorials/progress", params={"key": EXAMPLE}).status_code == 200
    assert EXAMPLE not in api.get("/api/tutorials").json()["progress"]["tutorials"]
    api.put("/api/tutorials/progress", json=body)
    assert api.delete("/api/tutorials/progress").json()["tutorials"] == {}


def test_progress_of_a_study_tutorial_needs_its_folder(api):
    r = api.put(
        "/api/tutorials/progress",
        json={"key": "study/short.yaml", "step": "one", "paused": False, "active": True},
    )
    assert r.status_code == 409


def test_writes_need_the_session(api):
    del api.headers[SESSION_HEADER]
    r = api.put("/api/tutorials/progress", json={"key": EXAMPLE, "step": "welcome"})
    assert r.status_code == 403
    assert api.delete("/api/tutorials/progress").status_code == 403


def test_an_unreadable_progress_file_means_no_progress(api):
    api.progress_file.parent.mkdir(parents=True, exist_ok=True)
    api.progress_file.write_text("{oops", "utf-8")
    assert api.get("/api/tutorials").json()["progress"]["tutorials"] == {}


# --- the engine never acts ------------------------------------------------------------------


def test_the_engine_calls_only_the_tutorial_endpoints():
    text = (STATIC / "js" / "tutorial.js").read_text("utf-8")
    assert re.search(r'^import \{ getJSON, send \} from "\./api\.js";$', text, re.M)
    assert "post(" not in text and "fetch(" not in text and "innerHTML" not in text
    urls = re.findall(r'(?:getJSON|send)\((?:"[A-Z]+", )?[`"]([^`"?$]+)', text)
    assert urls and all(u.startswith("api/tutorials") for u in urls), urls
    for forbidden in (
        ".click()",
        ".submit(",
        "dispatchEvent",
        "requestSubmit",
        ".checked =",
        ".value =",
        "selectedIndex",
    ):
        assert forbidden not in text, forbidden
