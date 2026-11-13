"""Tutorials (#69, ADR 0010): walkthroughs of the app's screens that teach, never act.

A tutorial is YAML (``tutorial-v1``): ordered steps of plain text that may point at a
control (a guidance target), show a view, or wait for a read-only condition. Workflow packs
ship examples (``WorkflowPack.tutorials``); a study keeps its own in ``tutorials/``. Both are
read through ``core.guidance`` and checked against the catalog.

Progress is per user, in ``<state>/tutorial-progress.json`` (``tutorial-progress-v1``): the
tutorial's key, hash and step id. It never holds a study path or data. The engine in the page
(``web/static/js/tutorial.js``) writes nothing else.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from pathlib import Path
from typing import Any

from opncafyr.core.guidance import (
    Catalog,
    list_study_files,
    parse_guidance,
    read_study_file,
    sha256_bytes,
    shipped_files,
)
from opncafyr.core.manifest import atomic_write_bytes, atomic_write_json, validate
from opncafyr.core.registry import StandardsCheck, StandardsType, WorkflowPack
from opncafyr.core.runs import now
from opncafyr.core.workspace import resolve_in_root

log = logging.getLogger(__name__)

SCHEMA = "tutorial-v1"
PROGRESS_SCHEMA = "tutorial-progress-v1"
PROGRESS_FILE = "tutorial-progress.json"
STUDY_DIR = "tutorials"
KEY_RE = re.compile(r"^(example/([a-z][a-z0-9_-]{0,31})|study)/([a-z][a-z0-9-]{0,63}\.yaml)$")


class TutorialError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def check_rules(doc: dict[str, Any], catalog: Catalog) -> list[str]:
    """Unique step ids, and every target, view and condition known to this app."""
    problems: list[str] = []
    seen: set[str] = set()
    refs: list[tuple[str, str, str]] = []
    for n, step in enumerate(doc["steps"]):
        where = f"steps/{n} ({step['id']})"
        if step["id"] in seen:
            problems.append(f"{where}: another step has the same id")
        seen.add(step["id"])
        for field, kind in (("target", "target"), ("view", "view"), ("wait_for", "condition")):
            if field in step:
                refs.append((f"{where}/{field}", kind, step[field]))
        target = catalog.targets.get(step.get("target", ""))
        if target and target.view and "view" in step and step["view"] != target.view:
            problems.append(f"{where}: the target is on another view than the step's view")
    return problems + catalog.unknown(refs)


def examine(data: bytes, label: str, catalog: Catalog) -> dict[str, Any]:
    doc, problems = parse_guidance(data, SCHEMA, label)
    if doc is not None:
        problems = check_rules(doc, catalog)
    out: dict[str, Any] = {"sha256": sha256_bytes(data), "problems": problems}
    if doc is not None:
        out.update(id=doc["id"], version=doc["version"], title=doc["title"])
        out.update(audience=doc.get("audience"), prerequisites=doc.get("prerequisites", []))
        out.update(profile=doc.get("profile"), steps=len(doc["steps"]), tutorial=doc)
    return out


def _labelled(name: str, problems: list[str]) -> list[str]:
    return [p if p.startswith(name) else f"{name}: {p}" for p in problems]


def check_standard(files: dict[str, bytes], ctx: StandardsCheck) -> tuple[list[str], list[str]]:
    """A standards bundle's tutorials (#70): each file checked as the study's own would be."""
    problems: list[str] = []
    for name, data in sorted(files.items()):
        problems.extend(_labelled(name, examine(data, name, ctx.catalog)["problems"]))
    return problems, []


STANDARDS_TYPE = StandardsType(
    id="tutorial",
    title="Tutorial",
    format="1.0",
    suffixes=(".yaml",),
    check=check_standard,
    install_to=STUDY_DIR,
)


class Tutorials:
    """Shipped and study tutorials, and the user's progress through them."""

    def __init__(self, packs: list[WorkflowPack], catalog: Catalog, progress: Path) -> None:
        self.catalog = catalog
        self.sources = {p.id: p.tutorials for p in packs if p.tutorials is not None}
        self.progress_path = Path(progress)
        self._lock = threading.Lock()

    # --- files --------------------------------------------------------------------------

    def _read(self, key: str, root: Path | None) -> bytes:
        match = KEY_RE.match(key)
        if not match:
            raise TutorialError("not_found", "There is no such tutorial.")
        pack, name = match.group(2), match.group(3)
        if pack is not None:
            data = shipped_files(self.sources.get(pack)).get(name)
            if data is None:
                raise TutorialError("not_found", "There is no such tutorial.")
            return data
        if root is None:
            raise TutorialError("no_workspace", "Open a study folder first.")
        try:
            return read_study_file(root, STUDY_DIR, name)
        except FileNotFoundError:
            raise TutorialError("not_found", "There is no such tutorial in tutorials/.") from None
        except ValueError as exc:
            raise TutorialError("invalid", str(exc)) from None

    def get(self, key: str, root: Path | None) -> dict[str, Any]:
        found = examine(self._read(key, root), key, self.catalog)
        return {"key": key, **found}

    def listing(self, root: Path | None) -> dict[str, Any]:
        def public(key: str, data: bytes) -> dict[str, Any]:
            found = examine(data, key, self.catalog)
            found.pop("tutorial", None)
            return {"key": key, **found}

        examples = [
            public(f"example/{pack}/{name}", data)
            for pack, source in sorted(self.sources.items())
            for name, data in shipped_files(source).items()
        ]
        study = []
        if root is not None:
            for name in list_study_files(root, STUDY_DIR):
                key = f"study/{name}"
                try:
                    study.append(public(key, self._read(key, root)))
                except TutorialError as exc:
                    study.append({"key": key, "problems": [exc.message]})
        return {"examples": examples, "study": study}

    def copy(self, key: str, root: Path) -> str:
        match = KEY_RE.match(key)
        if not match or match.group(2) is None:
            raise TutorialError("not_found", "Only an example can be copied.")
        data = self._read(key, root)
        name = match.group(3)
        target = resolve_in_root(root, f"{STUDY_DIR}/{name}")
        with self._lock:
            if target.exists():
                raise TutorialError(
                    "exists", f"{STUDY_DIR}/{name} already exists. It was not replaced."
                )
            atomic_write_bytes(target, data)
        return f"{STUDY_DIR}/{name}"

    # --- progress -----------------------------------------------------------------------

    def progress(self, *, for_write: bool = False) -> dict[str, Any]:
        """The saved progress. An unreadable file reads as none, with ``unreadable`` set;
        writing over it is refused (``for_write``), so one save can't wipe every
        tutorial's place. Reset all replaces it."""
        empty = {"schema_version": "1.0", "active": None, "tutorials": {}}
        try:
            doc = json.loads(self.progress_path.read_text("utf-8"))
            validate(doc, PROGRESS_SCHEMA)
        except FileNotFoundError:
            return empty
        except Exception:  # noqa: BLE001 - an unreadable file means "no progress"
            if for_write:
                raise TutorialError(
                    "progress_invalid",
                    "The saved tutorial progress can't be read, so it wasn't changed. "
                    "Reset all tutorial progress to start again.",
                ) from None
            log.warning("Ignoring an unreadable tutorial progress file")
            return {**empty, "unreadable": True}
        return doc

    def save(
        self,
        key: str,
        root: Path | None,
        step: str,
        *,
        paused: bool,
        active: bool,
        finished: bool = False,
    ) -> dict[str, Any]:
        """Record where the user is. The step must exist in the tutorial as it is now."""
        found = self.get(key, root)
        if found["problems"]:
            raise TutorialError("invalid", "This tutorial has problems; fix them first.")
        if step not in {s["id"] for s in found["tutorial"]["steps"]}:
            raise TutorialError("not_found", "The tutorial has no such step.")
        with self._lock:
            doc = self.progress(for_write=True)
            doc["tutorials"][key] = {
                "id": found["id"],
                "version": found["version"],
                "sha256": found["sha256"],
                "step": step,
                "paused": paused,
                "finished": finished,
                "updated_at": now(),
            }
            if active:
                doc["active"] = key
            elif doc["active"] == key:
                doc["active"] = None
            atomic_write_json(self.progress_path, doc, PROGRESS_SCHEMA)
        return doc

    def reset(self, key: str | None) -> dict[str, Any]:
        """Forget the progress of one tutorial, or of every tutorial."""
        with self._lock:
            if key is None:
                doc = {"schema_version": "1.0", "active": None, "tutorials": {}}
            else:
                doc = self.progress(for_write=True)
                doc["tutorials"].pop(key, None)
                if doc["active"] == key:
                    doc["active"] = None
            atomic_write_json(self.progress_path, doc, PROGRESS_SCHEMA)
        return doc
