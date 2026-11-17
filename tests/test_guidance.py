"""Guidance contracts shared by tutorials and study profiles (ADR 0010, #68, #69)."""

from __future__ import annotations

import os
import re
from importlib import resources
from pathlib import Path

import pytest

from nca_helpers import make_study, nca_client
from opncafyr.core.guidance import (
    MAX_GUIDANCE_BYTES,
    SHELL_CONDITIONS,
    SHELL_TARGETS,
    GuidanceError,
    build_catalog,
    list_study_files,
    parse_guidance,
    read_study_file,
)
from opncafyr.core.registry import (
    DescriptorError,
    GuideCondition,
    GuideTarget,
    ToolDescriptor,
    WorkflowPack,
    WorkflowView,
    load_descriptors,
    load_workflows,
)

CORE_STATIC = Path(str(resources.files("opncafyr.web").joinpath("static")))
SECRET = "SUBJ-0042"


def _texts(root: Path) -> str:
    return "\n".join(p.read_text("utf-8") for p in root.rglob("*") if p.suffix in {".js", ".html"})


def _pack_static(pack: WorkflowPack) -> Path:
    package, folder = pack.static
    return Path(str(resources.files(package).joinpath(folder)))


# --- the catalog -------------------------------------------------------------------------


def test_catalog_has_shell_and_pack_entries():
    catalog = build_catalog(load_workflows())
    assert "shell.workspace.pick" in catalog.targets
    assert "nca.mapping.save" in catalog.targets
    assert "nca.run.completed" in catalog.conditions
    assert {"home", "data", "tutorials", "analysis", "publish"} <= set(catalog.views)
    # Every pack view gets a sidenav target, made by the shell.
    assert "shell.nav.analysis" in catalog.targets
    listed = catalog.as_json()
    assert {t["id"] for t in listed["targets"]} == set(catalog.targets)


def test_a_duplicate_or_foreign_id_stops_the_catalog():
    view = WorkflowView(id="x", title="X", module="x.js")
    with pytest.raises(GuidanceError, match="Duplicate"):
        build_catalog(
            [WorkflowPack(id="p", targets=(GuideTarget("p.a", "A"), GuideTarget("p.a", "B")))]
        )
    with pytest.raises(GuidanceError, match="Invalid"):
        build_catalog([WorkflowPack(id="p", targets=(GuideTarget("shell.sneaky", "A"),))])
    with pytest.raises(GuidanceError, match="Invalid"):
        build_catalog([WorkflowPack(id="p", conditions=(GuideCondition("q.done", "A"),))])
    with pytest.raises(GuidanceError, match="can't be called"):
        build_catalog([WorkflowPack(id="shell")])
    with pytest.raises(GuidanceError, match="Duplicate view"):
        build_catalog([WorkflowPack(id="p", views=(view,)), WorkflowPack(id="q", views=(view,))])
    with pytest.raises(GuidanceError, match="Duplicate view"):
        build_catalog(
            [WorkflowPack(id="p", views=(WorkflowView(id="data", title="D", module="d.js"),))]
        )


# --- controls inside an embedded app (ADR 0013, #77) --------------------------------------


def _tool(tool_id="app", **guidance):
    data = {
        "schema_version": "1.0",
        "id": tool_id,
        "version": 1,
        "runtime": "python",
        "capabilities": ["thing"],
        "executables": ["python"],
        "health": {"kind": "python_import", "executable": "python", "target": "json"},
    }
    data["web_app"] = {"route": f"/apps/{tool_id}/", "readiness": "/h"}
    if guidance:
        data["web_app"]["guidance"] = guidance
    return ToolDescriptor.from_mapping(data, "test")


def test_a_tools_declared_ids_join_the_catalog_with_their_app():
    tool = _tool(
        targets=[{"id": "app.form.send", "title": "Send"}],
        conditions=[{"id": "app.form.sent", "title": "It was sent"}],
    )
    catalog = build_catalog(load_workflows(), [tool, _tool("bare")])
    assert catalog.targets["app.form.send"].app == "app"
    assert catalog.conditions["app.form.sent"].app == "app"
    listed = catalog.as_json()
    assert {"id": "app.form.send", "title": "Send", "view": None, "app": "app"} in listed["targets"]
    assert {"id": "app.form.sent", "title": "It was sent", "app": "app"} in listed["conditions"]
    assert listed["apps"] == [{"id": "app", "title": "app"}]
    # Page targets say they are not in an app.
    assert {t["app"] for t in listed["targets"] if t["id"].startswith("shell.")} == {None}


def test_the_shipped_tools_declare_their_guidance_ids():
    catalog = build_catalog(load_workflows(), load_descriptors().values())
    assert catalog.targets["opnca.export.save"].app == "opnca"
    assert catalog.conditions["opnca.run.saved"].app == "opnca"
    assert catalog.targets["deckifyr.build.run"].app == "deckifyr"
    assert set(catalog.apps) == {"opnca", "deckifyr"}


def test_a_tools_guidance_ids_must_be_its_own():
    with pytest.raises(DescriptorError, match="must start with the tool id"):
        _tool(targets=[{"id": "shell.sneaky", "title": "A"}])
    with pytest.raises(DescriptorError, match="must start with the tool id"):
        _tool(conditions=[{"id": "other.done", "title": "A"}])
    with pytest.raises(DescriptorError):
        _tool(targets=[{"id": "app.x", "title": "A", "selector": "#x"}])  # closed schema
    with pytest.raises(GuidanceError, match="same id as a workflow pack"):
        build_catalog([WorkflowPack(id="app")], [_tool(targets=[{"id": "app.x", "title": "A"}])])
    clash = [WorkflowPack(id="p", targets=(GuideTarget("p.a", "A"),))]
    tools = [_tool(targets=[{"id": "app.a", "title": "A"}, {"id": "app.a", "title": "B"}])]
    with pytest.raises(GuidanceError, match="Duplicate"):
        build_catalog(clash, tools)


def test_only_the_bridge_posts_to_another_window():
    bridge = (CORE_STATIC / "js" / "framebridge.js").read_text("utf-8")
    for path in CORE_STATIC.rglob("*.js"):
        if path.name != "framebridge.js":
            assert "postMessage" not in path.read_text("utf-8"), path.name
    # One post, to this page's own origin, of one fixed shape built in one place.
    assert bridge.count(".postMessage(") == 1
    assert "postMessage(message(op, id, n), location.origin)" in bridge
    shapes = re.findall(r"\{ type: \"guidance\", v: VERSION,[^}]*\}", bridge)
    assert shapes == [
        '{ type: "guidance", v: VERSION, op: "clear", seq: n }',
        '{ type: "guidance", v: VERSION, op, id, seq: n }',
    ]
    # A reply counts only from this origin and the asked frame's own window.
    assert "if (ev.origin !== location.origin) return;" in bridge
    assert "ev.source !== w.frame.contentWindow" in bridge
    # It never reaches into the app's page beyond asking whether it has loaded.
    for forbidden in ("innerHTML", "querySelector", "classList", ".click(", ".focus(", "eval("):
        assert forbidden not in bridge, forbidden
    assert bridge.count("contentDocument") == 1 and "readyState" in bridge


def test_unknown_references_are_named_by_place_not_value():
    catalog = build_catalog(load_workflows())
    problems = catalog.unknown(
        [
            ("steps/0/target", "target", SECRET),
            ("steps/1/wait_for", "condition", "nca.run.completed"),
            ("steps/2/view", "view", "analysis"),
            ("steps/3/view", "view", "https://example.org"),
        ]
    )
    assert problems == [
        "steps/0/target names no target this app has",
        "steps/3/view names no view this app has",
    ]


# --- every declared id is really on the page ------------------------------------------------


def test_every_declared_target_and_condition_is_marked_in_the_page():
    core = _texts(CORE_STATIC)
    for target in SHELL_TARGETS:
        assert f'"{target.id}"' in core, f"{target.id} is not marked in web/static"
    for condition in SHELL_CONDITIONS:
        assert f'registerCondition("{condition.id}"' in core, condition.id
    # Pack views' sidenav links are marked by workflows.js from the view id.
    assert "link.dataset.target = `shell.nav.${view.id}`" in core
    for pack in load_workflows():
        if pack.static is None:
            continue
        text = _texts(_pack_static(pack))
        for target in pack.targets:
            assert f'"{target.id}"' in text, f"{target.id} is not marked in {pack.id}"
        for condition in pack.conditions:
            assert f'registerCondition("{condition.id}"' in text, condition.id


def test_conditions_only_read_page_state():
    # A condition function must not fetch, post or change anything. The functions are
    # one-line arrows over page state; any API helper inside one is a mistake.
    pattern = re.compile(r'registerCondition\("[^"]+",\s*([^;]*?)\);', re.S)
    sources = [_texts(CORE_STATIC)] + [
        _texts(_pack_static(p)) for p in load_workflows() if p.static
    ]
    bodies = [m.group(1) for text in sources for m in pattern.finditer(text)]
    assert bodies
    for body in bodies:
        assert not re.search(r"\b(getJSON|post|send|fetch|location|emit)\b", body), body


# --- reading guidance files ----------------------------------------------------------------

SETTINGS_DOC = b'schema_version: "1.0"\ntheme: dark\n'


def test_parse_accepts_a_valid_document():
    doc, problems = parse_guidance(SETTINGS_DOC, "settings-v1", "settings.yaml")
    assert problems == [] and doc == {"schema_version": "1.0", "theme": "dark"}


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (b"x" * (MAX_GUIDANCE_BYTES + 1), "larger than"),
        (b"\xff\xfe" + SECRET.encode("utf-16-le"), "not UTF-8"),
        (f"- {SECRET}\n".encode(), "YAML mapping"),
        (b'schema_version: "1.0"\ntheme: [unclosed\n', "line"),
        (f'a: &x "{SECRET}"\nb: *x\n'.encode(), "aliases (* and &) are not allowed"),
        (f'schema_version: "1.0"\ntheme: {SECRET}\n'.encode(), "/theme fails 'enum'"),
        (f'schema_version: "1.0"\n{SECRET}: 1\n'.encode(), "additionalProperties"),
    ],
)
def test_parse_refuses_without_quoting_the_file(data, expected):
    doc, problems = parse_guidance(data, "settings-v1", "f.yaml")
    assert doc is None and problems
    assert any(expected in p for p in problems), problems
    assert not any(SECRET in p for p in problems), problems


def test_parse_refuses_python_tags():
    doc, problems = parse_guidance(
        b"theme: !!python/object/apply:os.system [echo]\n", "settings-v1", "f.yaml"
    )
    assert doc is None and problems


def test_study_files_stay_in_their_folder(tmp_path):
    root = tmp_path / "Study"
    (root / "tutorials").mkdir(parents=True)
    (root / "tutorials" / "first.yaml").write_bytes(SETTINGS_DOC)
    (root / "tutorials" / ".hidden.yaml").write_bytes(SETTINGS_DOC)
    (root / "tutorials" / "Upper.yaml").write_bytes(SETTINGS_DOC)
    (root / "tutorials" / "notes.txt").write_bytes(b"x")
    (root / "outside.yaml").write_bytes(SETTINGS_DOC)
    assert list_study_files(root, "tutorials") == ["first.yaml"]
    assert list_study_files(root, "profiles") == []
    assert read_study_file(root, "tutorials", "first.yaml") == SETTINGS_DOC
    for bad in ("../outside.yaml", ".hidden.yaml", "Upper.yaml", "a/b.yaml", "first.yml"):
        with pytest.raises(ValueError):
            read_study_file(root, "tutorials", bad)
    with pytest.raises(FileNotFoundError):
        read_study_file(root, "tutorials", "missing.yaml")
    (root / "tutorials" / "big.yaml").write_bytes(b"x" * (MAX_GUIDANCE_BYTES + 1))
    with pytest.raises(ValueError, match="larger"):
        read_study_file(root, "tutorials", "big.yaml")


@pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
def test_a_symlink_out_of_the_study_is_refused(tmp_path):
    root = tmp_path / "Study"
    (root / "tutorials").mkdir(parents=True)
    (tmp_path / "elsewhere.yaml").write_bytes(SETTINGS_DOC)
    (root / "tutorials" / "link.yaml").symlink_to(tmp_path / "elsewhere.yaml")
    with pytest.raises(ValueError):
        read_study_file(root, "tutorials", "link.yaml")


# --- the API -------------------------------------------------------------------------------


def test_guidance_catalog_route(security, tmp_path):
    with nca_client(security, tmp_path, make_study(tmp_path)) as client:
        body = client.get("/api/guidance").json()
    ids = {t["id"] for t in body["targets"]}
    assert {"shell.workspace.pick", "nca.start.button", "shell.nav.publish"} <= ids
    assert {"nca.mapping.ready", "shell.workspace.open"} <= {c["id"] for c in body["conditions"]}
    assert "analysis" in body["views"]
    assert {a["id"] for a in body["apps"]} == {"opnca", "deckifyr"}
    assert next(t for t in body["targets"] if t["id"] == "opnca.export.save")["app"] == "opnca"


@pytest.mark.parametrize(
    "text",
    [
        "version: 2026-11-31\n",  # a date that doesn't exist
        "version: " + "9" * 5000 + "\n",  # beyond Python's int limit
        "a: !!int abc\n",
        "a: !!float abc\n",
        "a: !!timestamp abc\n",
        "a: " + "[" * 2000 + "\n",  # deep nesting
        f"a: !foo!bar {SECRET}\n",  # an undefined tag handle
        f'a: "\\q{SECRET}"\n',  # an unknown escape
    ],
)
def test_odd_yaml_is_a_problem_never_a_crash(text):
    doc, problems = parse_guidance(text.encode(), "settings-v1", "f.yaml")
    assert doc is None and problems
    assert not any(SECRET in p or "9999" in p or "abc" in p for p in problems), problems


def test_values_are_read_as_text():
    # A version label like 1.0 or 2026-11-12 stays text, so the schema decides.
    doc, problems = parse_guidance(b"schema_version: 1.0\ntheme: dark\n", "settings-v1", "f.yaml")
    assert problems == [] and doc["schema_version"] == "1.0"
