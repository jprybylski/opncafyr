"""Guidance contracts shared by tutorials (#69) and study profiles (#68); ADR 0010.

Core knows three things about guidance, and nothing about what a workflow does:

- **Targets**: named controls and cards on the home page (``data-target="<id>"``). Core
  declares the shell's own (``shell.*``); each workflow pack declares its own.
- **Conditions**: read-only milestones a page reports from its in-memory state, such as
  "a study folder is open". Guidance only asks whether one holds.
- **Guidance files**: user-authored YAML (a tutorial, a study profile). They are data
  only. A file can name a target, a condition or a view from the catalog and nothing else:
  no code, URL, endpoint, path or command. They are read through ``read_guidance``, which
  caps the size, refuses YAML aliases, validates against a bundled schema and reports
  problems by place and rule, never by value.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any

from opncafyr.core.manifest import load_schema, schema_problems
from opncafyr.core.registry import GuideCondition, GuideTarget, WorkflowPack
from opncafyr.core.workspace import WorkspaceAccessError, resolve_in_root

MAX_GUIDANCE_BYTES = 256_000
FILE_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{0,63}\.yaml$")
SHELL = "shell"

# The shell's own views, as the router names them (web/static/js/router.js).
SHELL_VIEWS = ("home", "data", "jobs", "settings", "guide", "tutorials")

SHELL_TARGETS = (
    GuideTarget("shell.nav.home", "Home in the sidebar", None),
    GuideTarget("shell.nav.data", "Data in the sidebar", None),
    GuideTarget("shell.nav.jobs", "Jobs in the sidebar", None),
    GuideTarget("shell.nav.settings", "Settings in the sidebar", None),
    GuideTarget("shell.nav.guide", "Guide in the sidebar", None),
    GuideTarget("shell.nav.tutorials", "Tutorials in the sidebar", None),
    GuideTarget("shell.workspace.card", "The study folder card", "home"),
    GuideTarget("shell.workspace.pick", "Choose study folder…", "home"),
    GuideTarget("shell.data.datasets", "The list of data files", "data"),
    GuideTarget("shell.data.import", "Import (after a preview)", "data"),
    GuideTarget("shell.jobs.list", "The list of jobs", "jobs"),
    GuideTarget("shell.tool.toolstrip", "The bar above an embedded app", None),
    GuideTarget("shell.tool.newtab", "Open in new tab (embedded app)", None),
)

SHELL_CONDITIONS = (
    GuideCondition("shell.workspace.open", "A study folder is open"),
    GuideCondition("shell.dataset.imported", "The study has an imported data file"),
)


class GuidanceError(Exception):
    """A guidance catalog that can't be built (duplicate or invalid ids)."""


@dataclass(frozen=True)
class Catalog:
    """Every target, condition and view guidance may name, in this launcher."""

    targets: dict[str, GuideTarget]
    conditions: dict[str, GuideCondition]
    views: tuple[str, ...]
    # Pack ids, to tell a file's owner apart from a typo.
    owners: tuple[str, ...] = field(default=())

    def as_json(self) -> dict[str, Any]:
        return {
            "targets": [
                {"id": t.id, "title": t.title, "view": t.view} for t in self.targets.values()
            ],
            "conditions": [{"id": c.id, "title": c.title} for c in self.conditions.values()],
            "views": list(self.views),
        }

    def unknown(self, refs: Iterable[tuple[str, str, str]]) -> list[str]:
        """Problems for references the catalog lacks: ``(where, kind, id)`` triples.

        ``kind`` is "target", "condition" or "view". The message names the place and the
        kind only, so a mistyped value never appears in a log or an error.
        """
        known = {"target": self.targets, "condition": self.conditions, "view": self.views}
        return [
            f"{where} names no {kind} this app has"
            for where, kind, ref in refs
            if ref not in known[kind]
        ]


def build_catalog(packs: Iterable[WorkflowPack]) -> Catalog:
    """The catalog for the shell plus ``packs``. Refuses a duplicate or misplaced id."""
    targets: dict[str, GuideTarget] = {}
    conditions: dict[str, GuideCondition] = {}
    views = list(SHELL_VIEWS)
    owners: list[str] = []

    def add(owner: str, items: Iterable[Any], into: dict[str, Any], kind: str) -> None:
        for item in items:
            try:
                item.check(owner)
            except ValueError as exc:
                raise GuidanceError(str(exc)) from None
            if item.id in into:
                raise GuidanceError(f"Duplicate guidance {kind} id {item.id!r}.")
            into[item.id] = item

    add(SHELL, SHELL_TARGETS, targets, "target")
    add(SHELL, SHELL_CONDITIONS, conditions, "condition")
    for pack in packs:
        if pack.id == SHELL:
            raise GuidanceError(f"A workflow pack can't be called {SHELL!r}.")
        owners.append(pack.id)
        for view in pack.views:
            if view.id in views:
                raise GuidanceError(f"Duplicate view id {view.id!r}.")
            views.append(view.id)
            # The shell adds a sidenav link for every pack view (web/static/js/workflows.js).
            add(
                SHELL,
                [GuideTarget(f"shell.nav.{view.id}", f"{view.title} in the sidebar")],
                targets,
                "target",
            )
        add(pack.id, pack.targets, targets, "target")
        add(pack.id, pack.conditions, conditions, "condition")
    return Catalog(targets, conditions, tuple(views), tuple(owners))


# --- Guidance files --------------------------------------------------------------------


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _loader() -> type:
    import yaml

    class NoAliasLoader(yaml.SafeLoader):
        """``safe_load`` without anchors and aliases: a small file can't expand into a
        huge document, and a guidance file has no use for them."""

        def compose_node(self, parent: Any, index: Any) -> Any:
            if self.check_event(yaml.AliasEvent):
                event = self.peek_event()
                raise yaml.composer.ComposerError(
                    None, None, "aliases are not allowed", event.start_mark
                )
            return super().compose_node(parent, index)

    return NoAliasLoader


def parse_guidance(data: bytes, schema_name: str, label: str) -> tuple[Any, list[str]]:
    """Parse and validate one guidance file. Returns ``(document or None, problems)``.

    Problems name the line or the place in the document and the rule broken. They never
    quote the file's text: a user may have typed anything into it.
    """
    import yaml

    if len(data) > MAX_GUIDANCE_BYTES:
        return None, [f"{label} is larger than {MAX_GUIDANCE_BYTES // 1000} kB"]
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return None, [f"{label} is not UTF-8 text"]
    try:
        doc = yaml.load(text, Loader=_loader())  # noqa: S506 - SafeLoader subclass
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        where = f" on line {mark.line + 1}" if mark is not None else ""
        problem = getattr(exc, "problem", None) or "it is not valid YAML"
        return None, [f"{label}{where}: {problem}"]
    if not isinstance(doc, dict):
        return None, [f"{label} must be a YAML mapping (key: value lines)"]
    problems = schema_problems(doc, load_schema(schema_name), label)
    return (None if problems else doc), problems


def study_folder(root: Path, folder: str) -> Path:
    """A guidance folder at the top of the study (``profiles``, ``tutorials``)."""
    return resolve_in_root(root, folder)


def list_study_files(root: Path, folder: str) -> list[str]:
    """File names in a study's guidance folder that could be guidance files, sorted."""
    try:
        where = study_folder(root, folder)
    except WorkspaceAccessError:
        return []
    if not where.is_dir():
        return []
    return sorted(p.name for p in where.iterdir() if FILE_NAME_RE.match(p.name) and p.is_file())


def read_study_file(root: Path, folder: str, name: str) -> bytes:
    """The bytes of ``<folder>/<name>`` in the study, size-capped.

    Raises ``FileNotFoundError`` for a missing file and ``ValueError`` for a bad name or
    an oversize file. A name is a plain ``.yaml`` file name: no folders, no hidden files.
    """
    if not FILE_NAME_RE.match(name):
        raise ValueError("A guidance file name is lower-case letters, digits and dashes, .yaml.")
    try:
        path = resolve_in_root(root, f"{folder}/{name}")
    except WorkspaceAccessError:
        raise ValueError("That file is outside the study folder.") from None
    if not path.is_file():
        raise FileNotFoundError(name)
    if path.stat().st_size > MAX_GUIDANCE_BYTES:
        raise ValueError(f"The file is larger than {MAX_GUIDANCE_BYTES // 1000} kB.")
    return path.read_bytes()


def shipped_files(source: tuple[str, str] | None) -> dict[str, bytes]:
    """The example files a pack ships, ``{name: bytes}``. Read-only, installed code."""
    if source is None:
        return {}
    package, folder = source
    where = resources.files(package).joinpath(folder)
    if not where.is_dir():
        return {}
    return {
        entry.name: entry.read_bytes()
        for entry in sorted(where.iterdir(), key=lambda e: e.name)
        if FILE_NAME_RE.match(entry.name) and entry.is_file()
    }
