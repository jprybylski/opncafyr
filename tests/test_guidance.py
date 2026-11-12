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
    GuideCondition,
    GuideTarget,
    WorkflowPack,
    WorkflowView,
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
        if target.id == "shell.nav.tutorials":
            continue  # the tutorials link is added with the tutorial view (#69)
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
        (f'a: &x "{SECRET}"\nb: *x\n'.encode(), "aliases are not allowed"),
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
