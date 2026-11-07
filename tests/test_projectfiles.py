"""The project-file editor's API and file rules (#52), through the test-only toy_notes tool.

The toy adapter lists every ``.md`` it finds, links and outputs included; core must still
refuse what is unsafe. Nothing here is report-specific, which is the reuse proof: a
second tool gets the editor from its descriptor and adapter alone.
"""

from __future__ import annotations

import logging
import os
import re
import stat
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from opncafyr.core import manifest
from opncafyr.core.adapter import NewFileRule
from opncafyr.core.app import create_app
from opncafyr.core.jobmanager import JobManager
from opncafyr.core.projectfiles import sha256_bytes
from opncafyr.core.registry import DescriptorError
from opncafyr.core.runtime import Runtime
from opncafyr.core.security import SESSION_HEADER
from opncafyr.core.workspace import RecentWorkspaces
from toy_notes import ToyNotesEditor, toy_tools

SRC = Path(__file__).resolve().parents[1] / "src" / "opncafyr"
SECRET_TEXT = "Subject 1001 had a reaction"
needs_links = pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges")


def make_client(security, tmp_path, study, editor=None, open_study=True):
    jobs = JobManager(runtime=Runtime(), poll_interval=0.05)
    app = create_app(
        security,
        jobs=jobs,
        picker=lambda prompt, start: study,
        recent=RecentWorkspaces(tmp_path / "state" / "recent.json"),
        runtime=Runtime(),
        tools=toy_tools(),
        editors={"toy_notes": editor or ToyNotesEditor()},
        workflows=[],
    )
    client = TestClient(
        app,
        base_url=f"http://127.0.0.1:{security.port}",
        cookies={security.cookie_name: security.secret},
    )
    client.__enter__()
    client.headers[SESSION_HEADER] = security.secret
    if open_study:
        folder = client.post("/api/workspace/pick").json()["folder"]
        if client.post("/api/workspace/init", json={"path": folder["path"]}).status_code != 200:
            client.post("/api/workspace/open", json={"path": folder["path"]}).raise_for_status()
    client.jobs = jobs
    return client


# Fixture files are written as bytes: write_text would give them CRLF line ends on
# Windows, which the editor (rightly) keeps.
@pytest.fixture
def study(tmp_path):
    root = tmp_path / "study"
    (root / "notes" / "sub").mkdir(parents=True)
    (root / "notes" / "intro.md").write_bytes(b"# Intro\n\nHello.\n")
    (root / "notes" / "sub" / "more.md").write_bytes(b"More.\n")
    return root


@pytest.fixture
def api(security, tmp_path, study):
    client = make_client(security, tmp_path, study)
    yield client
    client.__exit__(None, None, None)
    client.jobs.shutdown()


def read(api, path):
    return api.post("/api/tools/toy_notes/project/read", json={"path": path})


def save(api, path, text, sha):
    return api.post(
        "/api/tools/toy_notes/project/save",
        json={"path": path, "text": text, "expected_sha256": sha},
    )


# --- listing, reading, saving -------------------------------------------------------------


def test_the_tool_tile_and_overview_come_from_the_descriptor(api):
    tools = {t["id"]: t for t in api.get("/api/tools").json()["tools"]}
    assert tools["toy_notes"]["project_editor"] == {"title": "Notes"}
    doc = api.get("/api/tools/toy_notes/project").json()
    assert doc["title"] == "Notes" and doc["project_exists"]
    assert [f["path"] for f in doc["files"]] == ["intro.md", "sub/more.md"]
    assert all("text" not in f for f in doc["files"])
    assert doc["settings"]["values"] == {"audience": None}


def test_read_save_round_trip_is_exact(api, study):
    doc = read(api, "intro.md").json()
    assert doc["text"] == "# Intro\n\nHello.\n" and doc["newline"] == "lf"
    new = "# Intro\n\nHello, *world*.\n"
    out = save(api, "intro.md", new, doc["sha256"])
    assert out.status_code == 200, out.text
    assert (study / "notes" / "intro.md").read_bytes() == new.encode()
    assert out.json()["sha256"] == sha256_bytes(new.encode())
    assert out.json()["warnings"] == []
    again = save(api, "intro.md", new + "TODO\n", out.json()["sha256"]).json()
    assert again["warnings"] == ["The note still has a TODO."]


def test_crlf_and_bom_are_kept(api, study):
    path = study / "notes" / "win.md"
    path.write_bytes(b"\xef\xbb\xbfOne\r\nTwo\r\n")
    doc = read(api, "win.md").json()
    assert (doc["text"], doc["newline"], doc["bom"]) == ("One\nTwo\n", "crlf", True)
    assert save(api, "win.md", "One\nTwo\nThree\n", doc["sha256"]).status_code == 200
    assert path.read_bytes() == b"\xef\xbb\xbfOne\r\nTwo\r\nThree\r\n"


@pytest.mark.parametrize(
    ("data", "reason"),
    [
        (b"One\r\nTwo\n", "line ends"),
        (b"\xff\xfe bad", "UTF-8"),
        (b"a\x00b", "text"),
        (b"x" * 5000, "Larger"),
    ],
)
def test_files_that_cannot_round_trip_open_read_only(api, study, data, reason):
    (study / "notes" / "odd.md").write_bytes(data)
    doc = read(api, "odd.md").json()
    assert doc["readonly"] and reason in doc["reason"] and doc["text"] is None
    sha = doc["sha256"] or sha256_bytes(data)
    resp = save(api, "odd.md", "replaced", sha)
    assert resp.status_code == 409
    assert (study / "notes" / "odd.md").read_bytes() == data


def test_a_save_over_the_size_cap_is_refused(api, study):
    doc = read(api, "intro.md").json()
    resp = save(api, "intro.md", "x" * 5000, doc["sha256"])
    assert resp.status_code == 413
    assert (study / "notes" / "intro.md").read_text("utf-8") == "# Intro\n\nHello.\n"


def test_a_stale_save_is_a_conflict_and_changes_nothing(api, study):
    doc = read(api, "intro.md").json()
    path = study / "notes" / "intro.md"
    path.write_bytes(b"Changed elsewhere.\n")
    resp = save(api, "intro.md", "My edit.\n", doc["sha256"])
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["code"] == "conflict"
    assert detail["current_sha256"] == sha256_bytes(b"Changed elsewhere.\n")
    assert path.read_text("utf-8") == "Changed elsewhere.\n"
    # Choosing "keep mine" saves against the disk revision.
    assert save(api, "intro.md", "My edit.\n", detail["current_sha256"]).status_code == 200
    assert path.read_text("utf-8") == "My edit.\n"


def test_an_interrupted_save_leaves_the_old_file_and_no_temp(api, study, monkeypatch):
    doc = read(api, "intro.md").json()

    def broken(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(manifest.os, "replace", broken)
    with pytest.raises(OSError):
        save(api, "intro.md", "New.\n", doc["sha256"])
    assert (study / "notes" / "intro.md").read_text("utf-8") == "# Intro\n\nHello.\n"
    assert [p.name for p in (study / "notes").iterdir() if p.name.endswith(".tmp")] == []


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")
def test_a_save_keeps_the_file_permissions(api, study):
    path = study / "notes" / "intro.md"
    path.chmod(0o640)
    doc = read(api, "intro.md").json()
    assert save(api, "intro.md", "New.\n", doc["sha256"]).status_code == 200
    assert stat.S_IMODE(path.stat().st_mode) == 0o640


# --- what can't be opened -----------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "../secret.md",
        "sub/../intro.md",
        "/etc/passwd",
        "C:/x.md",
        "sub\\more.md",
        ".hidden.md",
        "sub/.x.md",
        "OUTPUTS/table.md",
        "outputs/table.md",
        "unlisted.txt",
        "",
    ],
)
def test_unsafe_or_unlisted_paths_are_denied(security, tmp_path, study, path):
    (study / "notes" / ".hidden.md").write_bytes(b"x")
    (study / "notes" / "OUTPUTS").mkdir()
    (study / "notes" / "OUTPUTS" / "table.md").write_bytes(b"x")
    (study / "notes" / "unlisted.txt").write_bytes(b"x")
    (study / "secret.md").write_bytes(b"x")
    # The adapter offers the unsafe paths itself; "unlisted.txt" it never offers.
    offered = () if path == "unlisted.txt" else (path,)
    client = make_client(security, tmp_path, study, ToyNotesEditor(extra=offered))
    try:
        listed = [f["path"] for f in client.get("/api/tools/toy_notes/project").json()["files"]]
        assert path not in listed
        resp = read(client, path)
        assert resp.status_code in (403, 422), resp.text
        resp = save(client, path, "x", "0" * 64)
        assert resp.status_code in (403, 422)
    finally:
        client.__exit__(None, None, None)
        client.jobs.shutdown()


@needs_links
def test_links_are_never_followed(api, study, tmp_path):
    outside = tmp_path / "outside.md"
    outside.write_bytes(b"outside")
    (study / "notes" / "link.md").symlink_to(outside)
    (study / "notes" / "inner.md").symlink_to(study / "notes" / "intro.md")
    (tmp_path / "elsewhere").mkdir()
    (tmp_path / "elsewhere" / "deep.md").write_bytes(b"deep")
    (study / "notes" / "linkdir").symlink_to(tmp_path / "elsewhere")
    listed = [f["path"] for f in api.get("/api/tools/toy_notes/project").json()["files"]]
    for path in ("link.md", "inner.md", "linkdir/deep.md"):
        assert path not in listed
        assert read(api, path).status_code in (403, 404)
        assert save(api, path, "x", "0" * 64).status_code in (403, 404)
    assert outside.read_text("utf-8") == "outside"


@needs_links
def test_a_linked_project_folder_is_not_a_project(security, tmp_path):
    study = tmp_path / "study"
    study.mkdir()
    (tmp_path / "real").mkdir()
    (tmp_path / "real" / "a.md").write_bytes(b"a")
    (study / "notes").symlink_to(tmp_path / "real")
    client = make_client(security, tmp_path, study)
    try:
        assert client.get("/api/tools/toy_notes/project").json()["project_exists"] is False
        assert read(client, "a.md").status_code == 409
    finally:
        client.__exit__(None, None, None)
        client.jobs.shutdown()


def test_run_folders_and_metadata_are_out_of_reach(api, study):
    runs = study / ".opncafyr" / "runs" / "r1"
    runs.mkdir(parents=True)
    (runs / "notes.md").write_bytes(b"sealed")
    for path in ("../.opncafyr/runs/r1/notes.md", ".opncafyr/runs/r1/notes.md"):
        assert read(api, path).status_code == 403
    assert (runs / "notes.md").read_text("utf-8") == "sealed"


def test_tools_without_an_editor_and_closed_workspaces(security, tmp_path, study, api):
    assert api.get("/api/tools/nothing/project").status_code == 404
    assert api.get("/api/tools/Bad!/project").status_code == 422
    client = make_client(security, tmp_path, study, open_study=False)
    try:
        resp = client.get("/api/tools/toy_notes/project")
        assert resp.status_code == 409 and resp.json()["detail"]["code"] == "no_workspace"
    finally:
        client.__exit__(None, None, None)
        client.jobs.shutdown()


def test_saves_need_the_session_secret_and_the_right_origin(api, security):
    doc = read(api, "intro.md").json()
    body = {"path": "intro.md", "text": "x", "expected_sha256": doc["sha256"]}
    url = "/api/tools/toy_notes/project/save"
    assert api.post(url, json=body, headers={SESSION_HEADER: "wrong"}).status_code == 403
    assert api.post(url, json=body, headers={"origin": "http://evil.example"}).status_code == 403
    assert api.post(url, json=body, headers={"host": "evil.example"}).status_code == 400
    assert api.post(url, json=body, headers={"sec-fetch-site": "cross-site"}).status_code == 403
    assert read(api, "intro.md").json()["sha256"] == doc["sha256"]


def test_no_document_text_reaches_the_logs(api, caplog):
    caplog.set_level(logging.DEBUG)
    doc = read(api, "intro.md").json()
    save(api, "intro.md", SECRET_TEXT, doc["sha256"])
    assert SECRET_TEXT not in caplog.text
    assert "intro.md" in caplog.text  # the relative path is logged, the text is not


# --- settings ------------------------------------------------------------------------------


def test_settings_save_validate_and_conflict(api, study):
    url = "/api/tools/toy_notes/project/settings"
    bad = api.post(url, json={"values": {"audience": "everyone"}, "expected_sha256": None})
    assert bad.status_code == 422
    ok = api.post(url, json={"values": {"audience": "internal"}, "expected_sha256": None})
    assert ok.status_code == 200, ok.text
    assert ok.json()["values"] == {"audience": "internal"}
    sha = ok.json()["sha256"]
    assert sha == sha256_bytes((study / "notes" / "notes-settings.json").read_bytes())
    stale = api.post(url, json={"values": {"audience": "external"}, "expected_sha256": None})
    assert stale.status_code == 409 and stale.json()["detail"]["current_sha256"] == sha
    assert api.post(
        url, json={"values": {"audience": "external"}, "expected_sha256": sha}
    ).is_success
    (study / "notes" / "notes-settings.json").write_bytes(b'{"version": 9}')
    settings = api.get("/api/tools/toy_notes/project").json()["settings"]
    assert settings["error"] and settings["values"] == {}


# --- the contract --------------------------------------------------------------------------


def test_a_descriptor_naming_a_profile_the_adapter_lacks_stops_startup(security, tmp_path):
    class Other(ToyNotesEditor):
        profiles = frozenset({"other-v1"})

    with pytest.raises(DescriptorError, match="file_profile"):
        create_app(security, tools=toy_tools(), editors={"toy_notes": Other()}, workflows=[])
    with pytest.raises(DescriptorError, match="project_editor"):
        create_app(security, tools=toy_tools(), editors={}, workflows=[])


GENERIC = [
    SRC / "web" / "static" / "js" / "editor.js",
    SRC / "web" / "static" / "js" / "mdblocks.js",
    SRC / "web" / "static" / "js" / "unsaved.js",
    SRC / "core" / "projectfiles.py",
    SRC / "core" / "projectfiles_api.py",
]


@pytest.mark.parametrize("path", GENERIC, ids=lambda p: p.name)
def test_the_editor_names_no_tool(path):
    text = path.read_text("utf-8")
    for tool in ("opnca", "deckifyr", "quartifyr", "reportifyr", "toy_notes", "report"):
        assert not re.search(rf"\b{tool}\b", text), f"{path.name} names {tool}"


def test_os_replace_is_what_the_atomic_write_uses():
    # The interrupted-save test patches it there; keep them in step.
    assert manifest.os is os


# --- new files -----------------------------------------------------------------------------


def create(api, rule, name):
    return api.post("/api/tools/toy_notes/project/create", json={"rule": rule, "name": name})


def test_new_files_follow_the_adapters_rules(api, study):
    rules = api.get("/api/tools/toy_notes/project").json()["new_files"]
    assert rules == [
        {"rule": 0, "folder": "", "suffix": ".md", "label": "Note"},
        {"rule": 1, "folder": "drafts", "suffix": ".md", "label": "Draft"},
    ]
    out = create(api, 0, "ideas")
    assert out.status_code == 200, out.text
    doc = out.json()
    assert (doc["path"], doc["text"]) == ("ideas.md", "# New note\n")
    assert (study / "notes" / "ideas.md").read_bytes() == b"# New note\n"
    # The folder of a rule is made when missing; a typed suffix isn't doubled.
    assert create(api, 1, "first.md").json()["path"] == "drafts/first.md"
    listed = [f["path"] for f in api.get("/api/tools/toy_notes/project").json()["files"]]
    assert {"ideas.md", "drafts/first.md"} <= set(listed)
    # The new file saves like any other.
    assert save(api, "ideas.md", "# Ideas\n", doc["sha256"]).status_code == 200
    assert not [p for p in (study / "notes").rglob("*.tmp")]


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")
def test_a_new_file_is_readable_by_its_owner_and_group(api, study):
    create(api, 0, "perm")
    assert stat.S_IMODE((study / "notes" / "perm.md").stat().st_mode) == 0o644


def test_a_new_file_never_replaces_one(api, study):
    resp = create(api, 0, "intro")
    assert resp.status_code == 409 and resp.json()["detail"]["code"] == "exists"
    assert (study / "notes" / "intro.md").read_bytes() == b"# Intro\n\nHello.\n"


def test_without_hard_links_creation_still_refuses_to_overwrite(api, study, monkeypatch):
    def no_links(src, dst):
        raise OSError("hard links not supported")

    monkeypatch.setattr(os, "link", no_links)
    assert create(api, 0, "fallback").status_code == 200
    assert (study / "notes" / "fallback.md").read_bytes() == b"# New note\n"
    # A file that appears between the check and the write is still never replaced.
    from opncafyr.core import projectfiles

    with pytest.raises(projectfiles.ProjectFileError):
        projectfiles._write_new(study / "notes" / "intro.md", b"x")
    assert (study / "notes" / "intro.md").read_bytes() == b"# Intro\n\nHello.\n"


@pytest.mark.parametrize(
    "name", ["", "../up", "a/b", ".hidden", "-dash", "a b", "x" * 65, "C:x", "näme", "a\\b"]
)
def test_new_file_names_are_plain(api, study, name):
    resp = create(api, 0, name)
    assert resp.status_code == 422
    assert sorted(p.name for p in (study / "notes").iterdir()) == ["intro.md", "sub"]


def test_only_the_adapters_rules_can_create(security, tmp_path, study, api):
    assert create(api, 2, "x").status_code == 403
    unsafe = ToyNotesEditor(
        rules=(
            NewFileRule("../out", ".md", "Out", "md"),
            NewFileRule("OUTPUTS", ".md", "Outputs", "md"),
            NewFileRule("", "md", "No dot", "md"),
        )
    )
    client = make_client(security, tmp_path, study, unsafe)
    try:
        assert client.get("/api/tools/toy_notes/project").json()["new_files"] == []
        assert create(client, 0, "x").status_code == 403
    finally:
        client.__exit__(None, None, None)
        client.jobs.shutdown()


def test_a_created_file_the_adapter_doesnt_list_is_removed(security, tmp_path, study):
    client = make_client(security, tmp_path, study, ToyNotesEditor(hide=("ghost.md",)))
    try:
        resp = create(client, 0, "ghost")
        assert resp.status_code == 500 and resp.json()["detail"]["code"] == "not_listed"
        assert not (study / "notes" / "ghost.md").exists()
    finally:
        client.__exit__(None, None, None)
        client.jobs.shutdown()


@needs_links
def test_a_new_file_never_goes_through_a_linked_folder(api, study, tmp_path):
    (tmp_path / "elsewhere").mkdir()
    (study / "notes" / "drafts").symlink_to(tmp_path / "elsewhere")
    assert create(api, 1, "x").status_code == 403
    assert list((tmp_path / "elsewhere").iterdir()) == []


def test_editors_without_rules_offer_no_new_files(security, tmp_path, study):
    client = make_client(security, tmp_path, study, ToyNotesEditor(rules=()))
    try:
        assert client.get("/api/tools/toy_notes/project").json()["new_files"] == []
        assert create(client, 0, "x").status_code == 403
    finally:
        client.__exit__(None, None, None)
        client.jobs.shutdown()
