"""The project-file editor's file service (#52): which files may be opened, and safe saves.

A trusted descriptor opts a tool in with ``project_editor`` (``core.registry``); its
adapter's ``ProjectEditor`` lists the files it is willing to show. This module never
knows what a tool's files mean. It owns the rules that keep the editor inside them:

- **Paths.** A file is relative to the tool's project folder (one visible folder at the
  top of the workspace). No absolute paths, backslashes, ``..``, hidden or empty parts,
  and no ``OUTPUTS`` part. ``resolve_in_root`` checks containment, and every part from the
  workspace down is ``lstat``-ed so no link is followed. The file must also be on the
  adapter's current list: a request can't name a file the adapter didn't offer.
- **Content.** UTF-8 text only (a BOM is kept), no NUL, at most the file's byte cap.
  Browsers edit with ``\\n`` line ends; a CRLF file is converted back on save. A file
  mixing line ends opens read-only, since saving it would rewrite every line.
- **Saves.** The caller sends the SHA-256 it opened. Under the project's lock the file is
  hashed again; a different hash is a conflict (409) and nothing is written. The new
  bytes go through a synced temp file and a rename in the same folder, keep the file's
  permissions, and are read back and hashed before the save counts.
- **New files** (when the adapter supplies ``new_file_rules``). The user types a name
  only (letters, digits, ``-``, ``_``); the adapter's rule gives the folder, suffix and
  starter text. The file is written to a synced temp file and hard-linked into place, so
  an existing file is never overwritten, and it must then be on the adapter's list.
- **Snapshots.** Workflows that copy a project for a job hold ``lock(root, tool)`` while
  copying, so a save never lands half-way through a copy.
- **Privacy.** Logs name the tool, the relative path and byte counts, never the text.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import stat
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from opncafyr.core.adapter import (
    SYNTAXES,
    EditableFile,
    NewFileRule,
    ProjectEditor,
    SettingsInvalid,
    SupportsNewFiles,
)
from opncafyr.core.manifest import atomic_write_bytes, atomic_write_json
from opncafyr.core.registry import ProjectEditorSpec, ToolDescriptor
from opncafyr.core.workspace import WorkspaceAccessError, resolve_in_root

log = logging.getLogger(__name__)

BOM = b"\xef\xbb\xbf"
MAX_PATH_CHARS = 240
MAX_SETTINGS_BYTES = 64 * 1024
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
SUFFIX_RE = re.compile(r"^\.[a-z0-9]{1,8}$")
NEW_FILE_MODE = 0o644
# Folders no editor path may pass through, whatever the adapter lists.
FORBIDDEN_PARTS = frozenset({"outputs"})


class ProjectFileError(Exception):
    """A refused request. ``status`` is the HTTP status the API answers with."""

    def __init__(self, status: int, code: str, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.extra = extra

    def detail(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, **self.extra}


def _not_allowed() -> ProjectFileError:
    return ProjectFileError(403, "not_allowed", "That file can't be edited here.")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def check_relpath(rel: str) -> str:
    """A safe project-relative POSIX path, or ``ProjectFileError`` (403)."""
    if not isinstance(rel, str) or not rel or len(rel) > MAX_PATH_CHARS:
        raise _not_allowed()
    if "\\" in rel or "\x00" in rel or ":" in rel or rel.startswith("/"):
        raise _not_allowed()
    for part in rel.split("/"):
        if not part or part in (".", "..") or part.startswith("."):
            raise _not_allowed()
        if part.lower() in FORBIDDEN_PARTS:
            raise _not_allowed()
    return rel


def _no_links(root: Path, rel_parts: list[str]) -> Path:
    """``root/<parts>``, refusing any part that is a link (checked without following)."""
    path = root
    for part in rel_parts:
        path = path / part
        try:
            if stat.S_ISLNK(os.lstat(path).st_mode):
                raise _not_allowed()
        except FileNotFoundError:
            return path
    return path


@dataclass(frozen=True)
class Opened:
    """A file as the editor shows it."""

    path: str
    label: str
    syntax: str
    max_bytes: int
    data: bytes  # the whole file, or its first max_bytes + 1 bytes when larger
    size: int

    @property
    def sha256(self) -> str:
        return sha256_bytes(self.data)

    def decode(self) -> dict[str, Any]:
        """``text`` with ``\\n`` line ends plus how to write it back, or a read-only reason."""
        out: dict[str, Any] = {
            "path": self.path,
            "label": self.label,
            "syntax": self.syntax,
            "bytes": self.size,
            "max_bytes": self.max_bytes,
            "sha256": self.sha256,
            "bom": self.data.startswith(BOM),
            "newline": "lf",
            "readonly": False,
            "reason": None,
            "text": None,
        }
        if self.size > self.max_bytes or len(self.data) > self.max_bytes:
            reason = f"Larger than {self.max_bytes} bytes."
            return out | {"readonly": True, "reason": reason, "sha256": None}
        body = self.data[3:] if out["bom"] else self.data
        try:
            text = body.decode("utf-8")
        except UnicodeDecodeError:
            return out | {"readonly": True, "reason": "Not UTF-8 text."}
        if "\x00" in text:
            return out | {"readonly": True, "reason": "Not a text file."}
        if "\r" in text:
            rest = text.replace("\r\n", "")
            if "\r" in rest or "\n" in rest:
                reason = "Mixes Windows and Unix line ends; edit it in another editor."
                return out | {"readonly": True, "reason": reason}
            out["newline"] = "crlf"
            text = text.replace("\r\n", "\n")
        return out | {"text": text}


class ProjectFiles:
    """Project editors by tool id, with per-project locks."""

    def __init__(
        self, descriptors: Mapping[str, ToolDescriptor], editors: Mapping[str, ProjectEditor]
    ) -> None:
        self.specs: dict[str, ProjectEditorSpec] = {
            t: d.project_editor for t, d in descriptors.items() if d.project_editor is not None
        }
        self.editors = {t: e for t, e in editors.items() if t in self.specs}
        self._locks: dict[tuple[str, str], threading.Lock] = {}
        self._guard = threading.Lock()

    # --- lookups -----------------------------------------------------------------------

    def lock(self, root: Path, tool: str) -> threading.Lock:
        """The lock for one tool's project in one workspace (saves and job copies)."""
        key = (str(Path(root).resolve()), tool)
        with self._guard:
            return self._locks.setdefault(key, threading.Lock())

    def tools(self) -> list[str]:
        return sorted(self.editors)

    def _get(self, tool: str) -> tuple[ProjectEditorSpec, ProjectEditor]:
        if tool not in self.editors:
            raise ProjectFileError(404, "not_found", "This tool has no project editor.")
        return self.specs[tool], self.editors[tool]

    def project_folder(self, root: Path, tool: str) -> Path | None:
        """The tool's project folder, or None when it doesn't exist (or is a link)."""
        spec, _ = self._get(tool)
        try:
            resolve_in_root(root, spec.project_root)
        except WorkspaceAccessError:
            return None
        folder = Path(root).resolve() / spec.project_root
        try:
            st = os.lstat(folder)
        except FileNotFoundError:
            return None
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
            return None
        return folder

    def _listed(self, root: Path, tool: str) -> tuple[Path, dict[str, EditableFile]]:
        spec, editor = self._get(tool)
        folder = self.project_folder(root, tool)
        if folder is None:
            raise ProjectFileError(409, "no_project", "There is no project to edit yet.")
        listed: dict[str, EditableFile] = {}
        for f in editor.files(folder, spec.file_profile):
            try:
                check_relpath(f.path)
            except ProjectFileError:
                log.warning("Project editor %s offered an unsafe path; skipped", tool)
                continue
            if f.syntax in SYNTAXES:
                listed.setdefault(f.path, f)
        return folder, listed

    def _resolve(self, root: Path, tool: str, rel: str) -> tuple[Path, EditableFile]:
        check_relpath(rel)
        folder, listed = self._listed(root, tool)
        entry = listed.get(rel)
        if entry is None:
            raise _not_allowed()
        spec = self.specs[tool]
        try:
            resolve_in_root(root, f"{spec.project_root}/{rel}")
        except WorkspaceAccessError:
            raise _not_allowed() from None
        path = _no_links(folder, rel.split("/"))
        try:
            if not stat.S_ISREG(os.lstat(path).st_mode):
                raise _not_allowed()
        except FileNotFoundError:
            raise ProjectFileError(404, "not_found", "That file no longer exists.") from None
        return path, entry

    def _open(self, root: Path, tool: str, rel: str) -> tuple[Path, Opened]:
        path, entry = self._resolve(root, tool, rel)
        size = os.lstat(path).st_size
        # Read a little past the cap so an oversized file is reported, not loaded whole.
        with open(path, "rb") as fh:
            data = fh.read(min(size, entry.max_bytes) + 1)
        return path, Opened(entry.path, entry.label, entry.syntax, entry.max_bytes, data, size)

    # --- the API's operations ----------------------------------------------------------

    def overview(self, root: Path, tool: str) -> dict[str, Any]:
        spec, editor = self._get(tool)
        out: dict[str, Any] = {
            "tool": tool,
            "title": editor.title(spec.file_profile),
            "project_exists": self.project_folder(root, tool) is not None,
            "files": [],
            "settings": None,
        }
        if not out["project_exists"]:
            return out
        _, listed = self._listed(root, tool)
        for rel in listed:
            try:
                _, opened = self._open(root, tool, rel)
            except ProjectFileError:
                continue
            doc = opened.decode()
            doc.pop("text")
            out["files"].append(doc)
        if spec.settings_schema is not None:
            out["settings"] = self.settings(root, tool)
        out["new_files"] = [
            {"rule": i, "folder": r.folder, "suffix": r.suffix, "label": r.label}
            for i, r in enumerate(self._rules(tool))
        ]
        return out

    def read(self, root: Path, tool: str, rel: str) -> dict[str, Any]:
        _, opened = self._open(root, tool, rel)
        return opened.decode()

    def save(self, root: Path, tool: str, rel: str, text: str, expected: str) -> dict[str, Any]:
        spec, editor = self._get(tool)
        if not SHA_RE.match(expected or ""):
            raise ProjectFileError(422, "bad_revision", "The file's revision is missing.")
        if "\x00" in text:
            raise ProjectFileError(422, "not_text", "Text can't contain a NUL character.")
        with self.lock(root, tool):
            path, current = self._open(root, tool, rel)
            shape = current.decode()
            if shape["sha256"] != expected:
                raise ProjectFileError(
                    409,
                    "conflict",
                    "The file changed on disk since you opened it.",
                    current_sha256=shape["sha256"],
                )
            if shape["readonly"]:
                raise ProjectFileError(409, "read_only", shape["reason"])
            plain = text.replace("\r\n", "\n")
            body = plain.replace("\n", "\r\n") if shape["newline"] == "crlf" else plain
            data = (BOM if shape["bom"] else b"") + body.encode("utf-8")
            if len(data) > current.max_bytes:
                raise ProjectFileError(
                    413, "too_large", f"The file can be at most {current.max_bytes} bytes."
                )
            mode = stat.S_IMODE(os.lstat(path).st_mode)
            atomic_write_bytes(path, data, mode=mode, make_parents=False)
            _, saved = self._open(root, tool, rel)
            if saved.sha256 != sha256_bytes(data):
                raise ProjectFileError(500, "save_unverified", "The saved file doesn't match.")
        log.info("Saved %s project file %s (%d bytes)", tool, rel, len(data))
        warnings = [str(w) for w in editor.check_text(spec.file_profile, rel, plain)][:20]
        return {"path": rel, "sha256": saved.sha256, "bytes": len(data), "warnings": warnings}

    # --- new files ---------------------------------------------------------------------

    def _rules(self, tool: str) -> list[NewFileRule]:
        spec, editor = self._get(tool)
        if not isinstance(editor, SupportsNewFiles):
            return []
        rules = []
        for rule in editor.new_file_rules(spec.file_profile):
            try:
                if rule.folder:
                    check_relpath(rule.folder)
            except ProjectFileError:
                log.warning("Project editor %s offered an unsafe folder; skipped", tool)
                continue
            if SUFFIX_RE.match(rule.suffix) and rule.syntax in SYNTAXES:
                rules.append(rule)
        return rules

    def create(self, root: Path, tool: str, rule_index: int, name: str) -> dict[str, Any]:
        """Create a file from rule ``rule_index`` named ``name`` (+ the rule's suffix)."""
        rules = self._rules(tool)
        if not 0 <= rule_index < len(rules):
            raise ProjectFileError(403, "not_allowed", "New files can't be made here.")
        rule = rules[rule_index]
        if name.lower().endswith(rule.suffix):
            name = name[: -len(rule.suffix)]
        if not NAME_RE.match(name):
            raise ProjectFileError(
                422,
                "bad_name",
                "Use letters, digits, - and _ only (at most 64), starting with a letter or digit.",
            )
        rel = check_relpath(f"{rule.folder}/{name}{rule.suffix}".lstrip("/"))
        data = rule.starter.replace("\r\n", "\n").encode("utf-8")
        if len(data) > rule.max_bytes:
            raise ProjectFileError(500, "bad_rule", "The tool's starter text is too large.")
        spec = self.specs[tool]
        with self.lock(root, tool):
            project = self.project_folder(root, tool)
            if project is None:
                raise ProjectFileError(409, "no_project", "There is no project to edit yet.")
            try:
                resolve_in_root(root, f"{spec.project_root}/{rel}")
            except WorkspaceAccessError:
                raise _not_allowed() from None
            parts = rel.split("/")
            folder = project
            for part in parts[:-1]:  # the rule's folder: create it, never through a link
                folder = _no_links(folder, [part])
                if not folder.exists():
                    folder.mkdir()
                elif not folder.is_dir():
                    raise _not_allowed()
            target = folder / parts[-1]
            if os.path.lexists(target):
                raise ProjectFileError(409, "exists", f"{rel} already exists.")
            _write_new(target, data)
            if rel not in self._listed(root, tool)[1]:
                target.unlink()
                raise ProjectFileError(500, "not_listed", "The tool doesn't offer that file.")
        log.info("Created %s project file %s (%d bytes)", tool, rel, len(data))
        return self.read(root, tool, rel)

    # --- settings ----------------------------------------------------------------------

    def _settings_path(self, root: Path, tool: str) -> tuple[str, Path]:
        spec, editor = self._get(tool)
        assert spec.settings_schema is not None  # noqa: S101
        folder = self.project_folder(root, tool)
        if folder is None:
            raise ProjectFileError(409, "no_project", "There is no project to edit yet.")
        rel = check_relpath(editor.settings_file(spec.settings_schema))
        try:
            resolve_in_root(root, f"{spec.project_root}/{rel}")
        except WorkspaceAccessError:
            raise _not_allowed() from None
        return rel, _no_links(folder, rel.split("/"))

    def _settings_bytes(self, path: Path) -> bytes | None:
        try:
            st = os.lstat(path)
        except FileNotFoundError:
            return None
        if not stat.S_ISREG(st.st_mode) or st.st_size > MAX_SETTINGS_BYTES:
            raise ProjectFileError(409, "settings_invalid", "The settings file can't be read.")
        return path.read_bytes()

    def settings(self, root: Path, tool: str) -> dict[str, Any] | None:
        spec, editor = self._get(tool)
        if spec.settings_schema is None:
            return None
        rel, path = self._settings_path(root, tool)
        data = self._settings_bytes(path)
        out: dict[str, Any] = {
            "path": rel,
            "fields": [
                {
                    "name": f.name,
                    "label": f.label,
                    "help": f.help,
                    "required": f.required,
                    "options": [list(o) for o in f.options],
                }
                for f in editor.settings_form(spec.settings_schema)
            ],
            "values": {},
            "sha256": sha256_bytes(data) if data is not None else None,
            "error": None,
        }
        try:
            document = json.loads(data.decode("utf-8")) if data is not None else None
            out["values"] = editor.settings_values(spec.settings_schema, document)
        except (ValueError, SettingsInvalid) as exc:
            out["error"] = f"The saved settings are not valid: {exc}"
        return out

    def save_settings(
        self, root: Path, tool: str, values: Mapping[str, Any], expected: str | None
    ) -> dict[str, Any]:
        spec, editor = self._get(tool)
        if spec.settings_schema is None:
            raise ProjectFileError(404, "not_found", "This tool has no project settings.")
        try:
            document = editor.settings_document(spec.settings_schema, values)
        except SettingsInvalid as exc:
            raise ProjectFileError(422, "settings_invalid", str(exc)) from None
        with self.lock(root, tool):
            rel, path = self._settings_path(root, tool)
            data = self._settings_bytes(path)
            current = sha256_bytes(data) if data is not None else None
            if current != expected:
                raise ProjectFileError(
                    409,
                    "conflict",
                    "The settings changed since you opened them.",
                    current_sha256=current,
                )
            atomic_write_json(path, document)
        log.info("Saved %s project settings %s", tool, rel)
        return self.settings(root, tool) or {}


def _write_new(target: Path, data: bytes) -> None:
    """Write ``data`` to a new file at ``target``; never replace an existing one.

    The bytes go to a synced temp file, which is then hard-linked to ``target`` (the link
    fails if ``target`` exists). Without hard links (some network or FAT drives) it falls
    back to an exclusive create.
    """
    import tempfile

    fd, tmp = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, NEW_FILE_MODE)
        try:
            os.link(tmp, target)
        except FileExistsError:
            raise ProjectFileError(409, "exists", "That file already exists.") from None
        except OSError:
            try:
                with open(target, "xb") as fh:  # exclusive: fails if it exists
                    fh.write(data)
                    fh.flush()
                    os.fsync(fh.fileno())
            except FileExistsError:
                raise ProjectFileError(409, "exists", "That file already exists.") from None
    finally:
        Path(tmp).unlink(missing_ok=True)
