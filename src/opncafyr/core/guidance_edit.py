"""Editing guidance files in the app (#76, ADR 0010): a structured form, written here.

The page edits a study profile or a tutorial as a form built from a spec this module
serves (``GET <prefix>/form``). Targets, views and conditions are picked from the catalog
by title, so the form can't make up an id. The page never writes YAML: it sends the
document, and the server turns it into text (``dump_guidance``), checks those exact bytes
with the same ``examine`` the listings use, and writes them only when nothing is wrong.

A save writes one file, ``<folder>/<name>.yaml`` in the study, atomically. A new file
never replaces one, and an edit only replaces the revision it opened (``expected_sha256``).
Nothing else changes: no run, publication or progress. Editing the followed profile makes
the Study profile page ask to adopt it, and editing a tutorial in progress gets the
tutorial's "this tutorial changed" note; both come from the file's SHA-256 as before.

Workflow packs mount these routes for their own file types (``mount_guidance_editor``), so
core knows nothing about what a profile step means.
"""

from __future__ import annotations

import re
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from opncafyr.core.guidance import (
    FILE_NAME_RE,
    MAX_GUIDANCE_BYTES,
    MAX_GUIDANCE_FILES,
    list_study_files,
    read_study_file,
    sha256_bytes,
    study_folder,
)
from opncafyr.core.manifest import atomic_write_bytes
from opncafyr.core.workspace import WorkspaceAccessError, resolve_in_root

FILE = FILE_NAME_RE.pattern
SHA256 = r"^[0-9a-f]{64}$"
# Field kinds the page knows how to draw. Each names what the field holds, not a widget.
KINDS = {
    "name",  # an id: lower-case letters, digits and dashes
    "version",  # an author's version label
    "line",  # one line of text
    "text",  # a paragraph
    "target",  # a guidance target, picked by title
    "condition",  # a guidance condition, picked by title
    "view",  # a view of the app
    "items",  # checklist items: id and text
    "slots",  # run slots of earlier run steps
    "lines",  # a list of lines
}
_MAX_DEPTH = 5
_STEP = re.compile(r"(?:^|[ /])steps/(\d+)")


class GuidanceWriteError(Exception):
    def __init__(self, code: str, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.extra = extra


# --- the form spec -----------------------------------------------------------------------


def field(
    name: str,
    kind: str,
    label: str,
    *,
    required: bool = False,
    max: int | None = None,  # noqa: A002 - the JSON key the page reads
    help: str | None = None,  # noqa: A002
) -> dict[str, Any]:
    if kind not in KINDS:
        raise ValueError(f"unknown field kind {kind}")
    out: dict[str, Any] = {"name": name, "kind": kind, "label": label, "required": required}
    if max is not None:
        out["max"] = max
    if help:
        out["help"] = help
    return out


def form_spec(
    *,
    noun: str,
    document: list[dict[str, Any]],
    step: list[dict[str, Any]],
    step_types: list[dict[str, Any]] | None = None,
    max_steps: int = 100,
) -> dict[str, Any]:
    """What the page draws. ``step`` holds every step's fields; with ``step_types``, each
    type (``{"id", "title", "fields"}``) adds its own, and a step has a ``type`` field."""
    return {
        "noun": noun,
        "document": document,
        "step": step,
        "step_types": step_types,
        "max_steps": max_steps,
    }


def _key_order(spec: dict[str, Any]) -> tuple[list[str], list[str]]:
    doc = ["schema_version", *(f["name"] for f in spec["document"]), "steps"]
    step = [f["name"] for f in spec["step"]]
    for t in spec["step_types"] or []:
        step += [f["name"] for f in t["fields"] if f["name"] not in step]
    return doc, step


# --- the file text -----------------------------------------------------------------------


def _shape_problems(value: Any, where: str, known: set[str], depth: int = 0) -> list[str]:
    """Guidance is text, lists and mappings only. Anything else is refused by place; a key
    is named only when it is one of the form's field names, since anything else could be
    text someone typed."""
    if depth > _MAX_DEPTH:
        return [f"{where or 'the file'} is nested too deeply"]
    if isinstance(value, str):
        return []
    if isinstance(value, list):
        return [
            p
            for i, v in enumerate(value)
            for p in _shape_problems(v, f"{where}/{i}", known, depth + 1)
        ]
    if isinstance(value, dict):
        return [
            p
            for k, v in value.items()
            for p in _shape_problems(v, f"{where}/{k if k in known else '?'}", known, depth + 1)
        ]
    return [f"{where or 'the file'} must be text"]


def dump_guidance(doc: dict[str, Any], spec: dict[str, Any]) -> bytes:
    """The YAML text for a document, keys in the spec's order. Every value is a string,
    quoted where YAML would read it as something else, so the text-only loader reads back
    exactly this document. A paragraph with line breaks is written as a ``|`` block."""
    import yaml

    doc_order, step_order = _key_order(spec)

    def ordered(d: dict[str, Any], order: list[str]) -> dict[str, Any]:
        out = {k: d[k] for k in order if k in d}
        out.update((k, v) for k, v in d.items() if k not in out)
        return out

    def item(i: Any) -> Any:
        return ordered(i, ["id", "text"]) if isinstance(i, dict) else i

    def step(s: Any) -> Any:
        if not isinstance(s, dict):
            return s
        s = ordered(s, step_order)
        if isinstance(s.get("items"), list):
            s["items"] = [item(i) for i in s["items"]]
        return s

    out = ordered(doc, doc_order)
    if isinstance(out.get("steps"), list):
        out["steps"] = [step(s) for s in out["steps"]]

    class Dumper(yaml.SafeDumper):
        def increase_indent(self, flow: bool = False, indentless: bool = False) -> Any:
            return super().increase_indent(flow, False)

    def text(dumper: yaml.SafeDumper, value: str) -> Any:
        style = "|" if "\n" in value else None
        return dumper.represent_scalar("tag:yaml.org,2002:str", value, style=style)

    Dumper.add_representer(str, text)
    return yaml.dump(out, Dumper=Dumper, sort_keys=False, allow_unicode=True, width=88).encode(
        "utf-8"
    )


def comment_lines(data: bytes) -> int:
    """Lines that are YAML comments. Saving from the form doesn't keep them."""
    text = data.decode("utf-8", errors="replace")
    return sum(1 for line in text.splitlines() if line.lstrip().startswith("#"))


def locate(problems: list[str]) -> list[dict[str, Any]]:
    """Each problem with the step it is about (``steps/N`` in its text), if any."""
    out = []
    for p in problems:
        m = _STEP.search(p)
        out.append({"step": int(m.group(1)) if m else None, "text": p})
    return out


# --- saving ------------------------------------------------------------------------------


def save_study_file(
    root: Path,
    folder: str,
    name: str,
    data: bytes,
    *,
    expected_sha256: str | None,
    lock: threading.Lock,
) -> str:
    """Write ``<folder>/<name>`` in the study. Returns the new SHA-256.

    With ``expected_sha256`` None the file must not exist (a new file never replaces one);
    otherwise the file must still be the revision with that hash. The file must sit
    directly in the folder: no other folder, no link.
    """
    if not FILE_NAME_RE.match(name):
        raise GuidanceWriteError(
            "invalid_name",
            "A file name is lower-case letters, digits and dashes, ending in .yaml.",
        )
    if len(data) > MAX_GUIDANCE_BYTES:
        raise GuidanceWriteError(
            "too_large", f"The file would be larger than {MAX_GUIDANCE_BYTES // 1000} kB."
        )
    try:
        where = study_folder(root, folder)
        where.mkdir(exist_ok=True)
        where = study_folder(root, folder)
        target = resolve_in_root(root, f"{folder}/{name}")
    except (WorkspaceAccessError, OSError):
        raise GuidanceWriteError(
            "invalid_name", f"{folder}/ is not a folder inside the study."
        ) from None
    if not where.is_dir() or target.parent != where or (where / name).is_symlink():
        raise GuidanceWriteError("invalid_name", f"{folder}/{name} is not a file in {folder}/.")
    with lock:
        if expected_sha256 is None:
            if target.exists():
                raise GuidanceWriteError(
                    "exists",
                    f"{folder}/{name} already exists. It was not replaced; choose another name.",
                )
            if len(list_study_files(root, folder)) >= MAX_GUIDANCE_FILES:
                raise GuidanceWriteError(
                    "too_many", f"{folder}/ already has {MAX_GUIDANCE_FILES} files."
                )
        else:
            if not target.is_file():
                raise GuidanceWriteError(
                    "not_found", f"{folder}/{name} no longer exists. Save it as a new file."
                )
            current = sha256_bytes(target.read_bytes())
            if current != expected_sha256:
                raise GuidanceWriteError(
                    "conflict",
                    f"{folder}/{name} changed on disk since you opened it. Nothing was saved.",
                    current_sha256=current,
                )
        atomic_write_bytes(target, data, make_parents=False)
    return sha256_bytes(data)


# --- routes ------------------------------------------------------------------------------

STATUS = {
    "invalid_name": 422,
    "too_large": 422,
    "invalid": 422,
    "exists": 409,
    "too_many": 409,
    "conflict": 409,
    "not_found": 404,
    "no_workspace": 409,
}


@dataclass(frozen=True)
class GuidanceKind:
    """One kind of guidance file a pack lets the page edit."""

    folder: str  # in the study: "profiles", "tutorials"
    spec: dict[str, Any]
    label: str  # how problems name an unsaved document: "This profile"
    doc_key: str  # where ``examine`` puts the parsed document: "profile", "tutorial"
    examine: Callable[[bytes, str], dict[str, Any]]  # (data, label) -> {problems, ...}
    examples: Callable[[], dict[str, bytes]]  # shipped examples by id
    lock: threading.Lock


class CheckBody(BaseModel):
    doc: dict[str, Any]


class SaveBody(BaseModel):
    name: str = Field(pattern=FILE)
    doc: dict[str, Any]
    expected_sha256: str | None = Field(default=None, pattern=SHA256)


def problems_of(kind: GuidanceKind, doc: dict[str, Any], label: str) -> tuple[bytes, list[str]]:
    """The file text for ``doc`` and everything wrong with it, as a listing would say."""
    doc_keys, step_keys = _key_order(kind.spec)
    shape = _shape_problems(doc, "", {*doc_keys, *step_keys, "text"})
    if shape:
        return b"", [f"{label}: {p.lstrip('/')}" for p in shape]
    data = dump_guidance(doc, kind.spec)
    return data, kind.examine(data, label)["problems"]


def mount_guidance_editor(
    app: FastAPI, prefix: str, kind: GuidanceKind, root: Callable[[], Path | None]
) -> None:
    """``GET form``, ``GET edit``, ``POST check`` and ``PUT edit`` under ``prefix``."""

    def fail(code: str, message: str, **extra: Any) -> HTTPException:
        return HTTPException(STATUS.get(code, 400), {"code": code, "message": message, **extra})

    def study() -> Path:
        where = root()
        if where is None:
            raise fail("no_workspace", "Open a study folder first.")
        return where

    def not_leased() -> None:
        # A tool changing workspace files right now (core.workspace_lease) goes first.
        lease = getattr(app.state, "workspace_lease", None)
        if lease is not None and lease.holder is not None:
            raise HTTPException(
                423,
                {
                    "code": "workspace_busy",
                    "message": f"{lease.holder} is changing workspace files. "
                    "Save again in a moment.",
                },
            )

    @app.get(f"{prefix}/form")
    def form() -> dict[str, Any]:
        return {"spec": kind.spec, "folder": kind.folder}

    @app.get(f"{prefix}/edit")
    def open_file(
        name: str | None = Query(default=None, pattern=FILE),
        example: str | None = Query(default=None, max_length=128),
    ) -> dict[str, Any]:
        if (name is None) == (example is None):
            raise fail("not_found", "Open a study file or an example.")
        if example is not None:
            data = kind.examples().get(example)
            if data is None:
                raise fail("not_found", "There is no such example.")
            label, sha, suggested = example, None, example.rsplit("/", 1)[-1]
        else:
            try:
                data = read_study_file(study(), kind.folder, name)
            except FileNotFoundError:
                raise fail("not_found", f"There is no {kind.folder}/{name}.") from None
            except ValueError as exc:
                raise fail("invalid_name", str(exc)) from None
            label, sha, suggested = f"{kind.folder}/{name}", sha256_bytes(data), name
        found = kind.examine(data, label)
        return {
            "name": suggested,
            "new": example is not None,
            "sha256": sha,
            "doc": found.get(kind.doc_key),
            "problems": locate(found["problems"]),
            "comment_lines": comment_lines(data),
        }

    @app.post(f"{prefix}/check")
    def check(body: CheckBody) -> dict[str, Any]:
        _, problems = problems_of(kind, body.doc, kind.label)
        return {"problems": locate(problems)}

    @app.put(f"{prefix}/edit")
    def save(body: SaveBody) -> dict[str, Any]:
        where = study()
        not_leased()
        data, problems = problems_of(kind, body.doc, f"{kind.folder}/{body.name}")
        if problems:
            raise fail(
                "invalid", "Nothing was saved: fix the problems listed.", problems=locate(problems)
            )
        try:
            sha = save_study_file(
                where,
                kind.folder,
                body.name,
                data,
                expected_sha256=body.expected_sha256,
                lock=kind.lock,
            )
        except GuidanceWriteError as exc:
            raise fail(exc.code, exc.message, **exc.extra) from None
        return {"name": body.name, "path": f"{kind.folder}/{body.name}", "sha256": sha}
