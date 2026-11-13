"""Standards bundles (#70, ADR 0011): versioned, shared study standards, imported on request.

A **standards bundle** is a folder (or an archive, or a Git repository subfolder) with a
``standards.yaml`` (``standards-bundle-v1``) listing components and the SHA-256 of every
file they carry. Component *types* come from core and the workflow packs
(``registry.StandardsType``): tutorials here, study profiles and report/deck templates in the
NCA pack. Core verifies, stores and copies; it never knows what a component means.

A report or deck template doesn't have to be copied into every bundle. A template component
may instead carry a ``ref``:
- a **bundle reference** names a template component of another bundle (a house style) by id,
  version range and optional hash pin. It resolves only against bundles already imported into
  the study; nothing is fetched because of it;
- a **tool-native reference** names a template source in deckifyr's or quartifyr's own layout
  (repository, ref, subfolder, optional commit and tree-hash pins). The user imports it as its
  own snapshot, from Git or a local copy, through the same verification.

The flow is always explicit and two-step: **inspect** reads the source into a staging folder
and returns a preview (source, commit, components, compatibility, hashes, the files that will
be copied and what each would do in the study); **install** applies that exact preview.
Nothing is written before every check has passed, and a failed import leaves what was there.

- Verified copies are kept read-only in ``.opncafyr/standards/<key>/`` (a *snapshot*), inside
  the study, with study-relative paths only. A later import of a new version or commit is a
  new snapshot; earlier ones stay usable, offline.
- ``.opncafyr/standards.json`` (``standards-state-v1``) lists snapshots, the files copied into
  the study's visible folders with the hash written (to tell a local edit from an unedited
  copy), and the template each type uses.
- A copied file never replaces a local edit or a file the study made itself: the preview marks
  it a conflict, and the user keeps theirs or installs the new one beside it.
- Import never adopts a profile, changes a setting or starts a job. Problems name places and
  rules, never file contents.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import secrets
import shutil
import stat
import tarfile
import threading
import time
import zipfile
from collections import OrderedDict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from opncafyr import __version__
from opncafyr.core.guidance import FILE_NAME_RE, parse_guidance
from opncafyr.core.manifest import atomic_write_bytes, atomic_write_json, validate
from opncafyr.core.registry import STANDARDS_TOOLS, StandardsCheck, StandardsType
from opncafyr.core.runs import now
from opncafyr.core.workspace import WORKSPACE_META_DIR, WorkspaceAccessError, resolve_in_root

SCHEMA = "standards-bundle-v1"
STATE_SCHEMA = "standards-state-v1"
MANIFEST = "standards.yaml"
STATE_FILE = f"{WORKSPACE_META_DIR}/standards.json"
STORE = f"{WORKSPACE_META_DIR}/standards"
STAGING_PREFIX = ".staging-"
FETCHED = ".fetched"
TEMPLATE_COMPONENT = "template"

MAX_FILES = 500
MAX_FILE_BYTES = 20_000_000
MAX_TOTAL_BYTES = 100_000_000
MAX_DEPTH = 8
MAX_DIFF_LINES = 300
PREVIEWS_KEPT = 3
PREVIEW_TTL_S = 1800
FETCHED_TTL_S = 24 * 3600

SEGMENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
KEY_RE = re.compile(
    r"^([a-z][a-z0-9-]{0,63}/[0-9A-Za-z][0-9A-Za-z.+-]{0,31}-[0-9a-f]{12}"
    r"|template-(deckifyr|quartifyr)/[0-9a-f]{12})$"
)
NAME_RE = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
FETCH_ID_RE = re.compile(r"^[0-9a-f]{16}$")
ARCHIVE_SUFFIXES = (".zip", ".tar", ".tar.gz", ".tgz")
_CLAUSE_RE = re.compile(r"^\s*(>=|<=|==|!=|>|<)\s*([0-9]+(?:\.[0-9]+){0,3})\s*$")


class StandardsError(Exception):
    """A standards request that can't be done. ``code`` maps to an HTTP status."""

    def __init__(self, code: str, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.extra = extra


# --- versions --------------------------------------------------------------------------------


def version_tuple(text: str) -> tuple[int, ...]:
    """The leading dotted numbers of a version (``0.3.7`` of ``0.3.7.dev1``), padded to 4."""
    match = re.match(r"^\s*(\d+(?:\.\d+)*)", text)
    parts = [int(p) for p in match.group(1).split(".")] if match else [0]
    return tuple((parts + [0, 0, 0, 0])[:4])


def in_range(version: str, spec: str) -> bool:
    """Whether ``version`` meets every clause of ``spec`` (``>=0.4, <1``)."""
    have = version_tuple(version)
    for clause in spec.split(","):
        match = _CLAUSE_RE.match(clause)
        if match is None:
            return False
        op, want = match.group(1), version_tuple(match.group(2))
        ok = {
            ">=": have >= want,
            "<=": have <= want,
            ">": have > want,
            "<": have < want,
            "==": have == want,
            "!=": have != want,
        }[op]
        if not ok:
            return False
    return True


# --- hashes and paths ------------------------------------------------------------------------


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def digest(entries: Mapping[str, str]) -> str:
    """One SHA-256 for a set of files: over sorted ``<path>  <sha256>`` lines."""
    lines = "\n".join(f"{path}  {sha}" for path, sha in sorted(entries.items()))
    return sha256_bytes(lines.encode("utf-8"))


def safe_relpath(rel: str) -> bool:
    """A plain relative path: visible names of letters, digits, ``.``, ``_``, ``-``."""
    parts = rel.split("/")
    return 0 < len(parts) <= MAX_DEPTH and all(SEGMENT_RE.match(p) for p in parts)


def folder_of(paths: Iterable[str]) -> str:
    """The deepest folder that holds every path (``""`` for the top)."""
    common: tuple[str, ...] | None = None
    for path in paths:
        parts = PurePosixPath(path).parent.parts
        if common is None:
            common = parts
        else:
            n = 0
            while n < min(len(common), len(parts)) and common[n] == parts[n]:
                n += 1
            common = common[:n]
    return "/".join(common or ())


def within(folder: str, path: str) -> str:
    return path[len(folder) + 1 :] if folder else path


def read_source_file(root: Path, rel: str) -> bytes:
    """The bytes of ``root/rel``: a regular file, reached through no link, size-capped."""
    if not safe_relpath(rel):
        raise StandardsError("unsafe_path", f"{rel!r} is not a plain path inside the bundle.")
    current = root
    info = None
    for part in rel.split("/"):
        current = current / part
        try:
            info = os.lstat(current)
        except FileNotFoundError:
            raise StandardsError(
                "missing_file", f"{rel} is listed but not in the source."
            ) from None
        if stat.S_ISLNK(info.st_mode):
            raise StandardsError("unsafe_path", f"{rel} is reached through a link.")
    assert info is not None  # noqa: S101
    if not stat.S_ISREG(info.st_mode):
        raise StandardsError("unsafe_path", f"{rel} is not a regular file.")
    if info.st_size > MAX_FILE_BYTES:
        raise StandardsError("too_large", f"{rel} is larger than {MAX_FILE_BYTES // 10**6} MB.")
    return current.read_bytes()


def list_tree(root: Path, suffixes: tuple[str, ...]) -> tuple[list[str], int]:
    """Regular files under ``root`` with an allowed suffix, and how many others were left
    out (hidden names, links, other kinds and suffixes). Never follows a link."""
    kept: list[str] = []
    skipped = 0

    def walk(folder: Path, prefix: str, depth: int) -> None:
        nonlocal skipped
        with os.scandir(folder) as entries:
            for entry in sorted(entries, key=lambda e: e.name):
                rel = f"{prefix}{entry.name}"
                if not SEGMENT_RE.match(entry.name) or entry.is_symlink():
                    skipped += 1
                elif entry.is_dir(follow_symlinks=False):
                    if depth >= MAX_DEPTH:
                        skipped += 1
                    else:
                        walk(Path(entry.path), f"{rel}/", depth + 1)
                elif entry.is_file(follow_symlinks=False) and rel.lower().endswith(suffixes):
                    kept.append(rel)
                    if len(kept) > MAX_FILES:
                        raise StandardsError(
                            "too_large",
                            f"The template has more than {MAX_FILES} files. Point the "
                            "subfolder at the template itself.",
                        )
                else:
                    skipped += 1

    walk(root, "", 1)
    return kept, skipped


def extract_archive(archive: Path, dest: Path) -> int:
    """Unpack the regular files of a ``.zip`` or ``.tar[.gz]`` into ``dest``.

    Only entries with plain names (``safe_relpath``) are written, so nothing can land
    outside ``dest``; links, devices and other names are left out and counted. Sizes are
    capped as they are read, not as the archive claims.
    """
    dest.mkdir(parents=True, exist_ok=True)
    skipped = 0
    total = 0
    count = 0

    def write(name: str, read: Any) -> None:
        nonlocal total, count
        count += 1
        if count > MAX_FILES * 4:
            raise StandardsError("too_large", "The archive has too many files.")
        data = read(MAX_FILE_BYTES + 1)
        if len(data) > MAX_FILE_BYTES:
            raise StandardsError("too_large", f"{name} in the archive is too large.")
        total += len(data)
        if total > MAX_TOTAL_BYTES:
            raise StandardsError("too_large", "The archive is too large.")
        target = dest.joinpath(*name.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            raise StandardsError("unsafe_path", f"The archive lists {name} twice.")
        target.write_bytes(data)

    lower = archive.name.lower()
    try:
        if lower.endswith(".zip"):
            with zipfile.ZipFile(archive) as zf:
                for info in zf.infolist():
                    name = info.filename.rstrip("/")
                    mode = info.external_attr >> 16
                    if info.is_dir():
                        continue
                    if stat.S_ISLNK(mode) or not safe_relpath(name):
                        skipped += 1
                        continue
                    with zf.open(info) as fh:
                        write(name, fh.read)
        else:
            with tarfile.open(archive, "r:*") as tf:
                for member in tf:
                    if member.isdir():
                        continue
                    name = member.name.removeprefix("./")
                    if not member.isfile() or not safe_relpath(name):
                        skipped += 1
                        continue
                    fh = tf.extractfile(member)
                    if fh is None:
                        skipped += 1
                        continue
                    with fh:
                        write(name, fh.read)
    except (zipfile.BadZipFile, tarfile.TarError, EOFError, OSError):
        raise StandardsError("bad_archive", "The archive can't be read.") from None
    return skipped


TEMPLATE_MARKERS = (
    "presentation.yaml",
    "templates",
    "_quarto.yml",
    "style.yaml",
    "org-reference.docx",
    "config.yaml",
    "standard_footnotes.yaml",
)


def top_folder(root: Path, markers: tuple[str, ...]) -> Path:
    """An unpacked archive's real top: itself, or its one folder when it holds none of
    ``markers`` and nothing else (``bundle-1.2/standards.yaml``)."""
    if any((root / m).exists() for m in markers):
        return root
    entries = list(root.iterdir())
    if len(entries) == 1 and entries[0].is_dir() and not entries[0].is_symlink():
        return entries[0]
    return root


# --- inspection ------------------------------------------------------------------------------


@dataclass
class Component:
    id: str
    type: str
    title: str
    description: str | None
    files: dict[str, str]  # bundle path -> sha256
    ref: dict[str, Any] | None
    links: list[str]
    folder: str

    @property
    def sha256(self) -> str | None:
        return digest(self.files) if self.files else None


@dataclass
class Inspection:
    """A verified source, ready to stage: every file to keep, with its bytes and hash."""

    kind: str  # "bundle" | "template"
    key: str
    title: str
    data: dict[str, bytes]
    components: dict[str, Component]
    problems: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    doc: dict[str, Any] | None = None
    tool: str | None = None
    native_type: str | None = None
    compatibility: list[dict[str, Any]] = field(default_factory=list)

    @property
    def hashes(self) -> dict[str, str]:
        return {path: sha256_bytes(data) for path, data in self.data.items()}

    @property
    def inventory_sha256(self) -> str:
        return digest(self.hashes)


def _check_requires(doc: dict[str, Any], types: Mapping[str, StandardsType]) -> list[dict]:
    rows = []
    requires = doc.get("requires") or {}
    if "opncafyr" in requires:
        rows.append(
            {
                "what": "opncafyr",
                "range": requires["opncafyr"],
                "have": __version__,
                "ok": in_range(__version__, requires["opncafyr"]),
            }
        )
    for type_id, spec in sorted((requires.get("types") or {}).items()):
        kind = types.get(type_id)
        have = kind.format if kind else None
        rows.append(
            {
                "what": f"{type_id} format",
                "range": spec,
                "have": have,
                "ok": have is not None and in_range(have, spec),
            }
        )
    return rows


def inspect_bundle(root: Path, types: Mapping[str, StandardsType], catalog: Any) -> Inspection:
    """Read and verify the bundle at ``root``. Problems are collected, not raised."""
    raw = read_source_file(root, MANIFEST)
    doc, problems = parse_guidance(raw, SCHEMA, MANIFEST)
    if doc is None:
        return Inspection("bundle", "", "", {}, {}, problems=problems)
    insp = Inspection("bundle", "", doc["title"], {MANIFEST: raw}, {}, doc=doc)
    problems = insp.problems
    insp.compatibility = _check_requires(doc, types)
    for row in insp.compatibility:
        if not row["ok"]:
            problems.append(
                f"requires {row['what']} {row['range']}, but this app has {row['have'] or 'none'}"
            )

    seen_paths: dict[str, str] = {MANIFEST.lower(): MANIFEST}
    destinations: dict[str, str] = {}
    total = len(raw)
    for n, entry in enumerate(doc["components"]):
        where = f"components/{n} ({entry['id']})"
        if entry["id"] in insp.components:
            problems.append(f"{where}: another component has the same id")
            continue
        kind = types.get(entry["type"])
        comp = Component(
            id=entry["id"],
            type=entry["type"],
            title=entry["title"],
            description=entry.get("description"),
            files={},
            ref=entry.get("ref"),
            links=list(entry.get("links", [])),
            folder="",
        )
        insp.components[comp.id] = comp
        if kind is None:
            problems.append(f"{where}: this app has no component type {entry['type']!r}")
            continue
        if comp.ref is not None:
            if kind.install_to is not None:
                problems.append(f"{where}: only a template can reference another source")
            elif "tool" in comp.ref and comp.ref["tool"] != kind.native:
                problems.append(f"{where}: a {kind.id} can't come from a {comp.ref['tool']} source")
            continue
        payload: dict[str, bytes] = {}
        for file in entry["files"]:
            path = file["path"]
            folded = path.lower()
            if folded in seen_paths:
                problems.append(f"{where}: {path} is listed twice in the bundle")
                continue
            seen_paths[folded] = path
            if not path.lower().endswith(kind.suffixes):
                problems.append(f"{where}: {path} is not a kind of file a {kind.id} holds")
                continue
            try:
                data = read_source_file(root, path)
            except StandardsError as exc:
                problems.append(f"{where}: {exc.message}")
                continue
            total += len(data)
            if sha256_bytes(data) != file["sha256"]:
                problems.append(f"{where}: {path} doesn't match its SHA-256 in {MANIFEST}")
                continue
            comp.files[path] = file["sha256"]
            payload[path] = data
        if total > MAX_TOTAL_BYTES or len(seen_paths) > MAX_FILES:
            raise StandardsError("too_large", "The bundle is too large to import.")
        comp.folder = folder_of(comp.files)
        if kind.install_to is not None:
            for path in comp.files:
                name = PurePosixPath(path).name
                dest = f"{kind.install_to}/{name}"
                if not FILE_NAME_RE.match(name):
                    problems.append(
                        f"{where}: {path} needs a lower-case name of letters, digits and "
                        "dashes ending .yaml to be copied into the study"
                    )
                elif dest in destinations:
                    problems.append(f"{where}: {path} would be copied to the same file as another")
                destinations[dest] = comp.id
        if len(payload) == len(entry["files"]):
            found, notes = kind.check(
                {within(comp.folder, p): d for p, d in payload.items()}, StandardsCheck(catalog)
            )
            problems.extend(f"{where}: {p}" for p in found)
            insp.notes.extend(f"{comp.id}: {note}" for note in notes)
        insp.data.update(payload)

    for comp in insp.components.values():
        for link in comp.links:
            if link == comp.id or link not in insp.components:
                problems.append(f"component {comp.id}: links to {link}, which isn't in the bundle")
    variant_ids: set[str] = set()
    for variant in doc.get("variants", []):
        if variant["id"] in variant_ids:
            problems.append(f"variant {variant['id']}: another variant has the same id")
        variant_ids.add(variant["id"])
        for cid in variant["components"]:
            if cid not in insp.components:
                problems.append(f"variant {variant['id']}: names {cid}, which isn't in the bundle")
    if not problems:
        insp.key = f"{doc['id']}/{doc['version']}-{insp.inventory_sha256[:12]}"
    return insp


def inspect_template(
    root: Path,
    tool: str,
    native_type: str | None,
    types: Mapping[str, StandardsType],
    catalog: Any,
) -> Inspection:
    """Read and verify a template source in ``tool``'s own layout at ``root``."""
    kind = next((t for t in types.values() if t.native == tool), None)
    if kind is None:
        raise StandardsError("unknown_type", f"This app takes no {tool} template.")
    kept, skipped = list_tree(root, kind.suffixes)
    if not kept:
        raise StandardsError("empty", f"The folder holds no {tool} template files.")
    data: dict[str, bytes] = {}
    total = 0
    for rel in kept:
        data[rel] = read_source_file(root, rel)
        total += len(data[rel])
        if total > MAX_TOTAL_BYTES:
            raise StandardsError("too_large", "The template is too large to import.")
    title = f"{tool} template" + (f" ({native_type})" if native_type else "")
    comp = Component(
        id=TEMPLATE_COMPONENT,
        type=kind.id,
        title=title,
        description=None,
        files={p: sha256_bytes(d) for p, d in data.items()},
        ref=None,
        links=[],
        folder="",
    )
    insp = Inspection(
        "template", "", title, data, {comp.id: comp}, tool=tool, native_type=native_type
    )
    if skipped:
        insp.notes.append(f"{skipped} other file(s) or folder(s) in the source are not copied")
    found, notes = kind.check(dict(data), StandardsCheck(catalog, True, native_type))
    insp.problems.extend(found)
    insp.notes.extend(notes)
    if not insp.problems:
        insp.key = f"template-{tool}/{insp.inventory_sha256[:12]}"
    return insp


# --- the study's record ----------------------------------------------------------------------


def empty_state() -> dict[str, Any]:
    return {"schema_version": "1.0", "snapshots": {}, "installed": {}, "selected": {}}


def read_state(root: Path) -> dict[str, Any]:
    from jsonschema import ValidationError

    path = resolve_in_root(root, STATE_FILE)
    if not path.is_file():
        return empty_state()
    try:
        doc = json.loads(path.read_text("utf-8"))
        validate(doc, STATE_SCHEMA)
    except (OSError, ValueError, ValidationError):
        raise StandardsError(
            "state_invalid",
            f"{STATE_FILE} is not valid, so imported standards can't be read or changed. "
            "Restore it from a backup or version history.",
        ) from None
    return doc


def write_state(root: Path, doc: dict[str, Any]) -> None:
    atomic_write_json(resolve_in_root(root, STATE_FILE), doc, STATE_SCHEMA)


def state_sha(root: Path) -> str:
    path = resolve_in_root(root, STATE_FILE)
    return sha256_bytes(path.read_bytes()) if path.is_file() else ""


def snapshot_dir(root: Path, key: str) -> Path:
    if not KEY_RE.match(key):
        raise StandardsError("not_found", "There is no such imported standard.")
    return resolve_in_root(root, f"{STORE}/{key}")


def _read_only(path: Path) -> None:
    mode = path.stat().st_mode
    path.chmod(mode & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))


def stage(staging: Path, data: Mapping[str, bytes]) -> None:
    for rel, content in data.items():
        target = staging.joinpath(*rel.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)


def files_digest(folder: Path, files: Iterable[str]) -> str | None:
    """The ``digest`` of ``files`` as they are now under ``folder``; None if one is missing
    or reached through a link."""
    entries = {}
    for rel in files:
        try:
            entries[rel] = sha256_bytes(read_source_file(folder, rel))
        except StandardsError:
            return None
    return digest(entries)


def promote(root: Path, staging: Path, insp: Inspection) -> bool:
    """Move a verified staging folder into place as a read-only snapshot.

    Returns False when the same snapshot is already there (the key holds its hash). A folder
    at the key that doesn't hold exactly these files is refused, never replaced.
    """
    target = snapshot_dir(root, insp.key)
    if target.exists():
        if files_digest(target, insp.data) != insp.inventory_sha256:
            raise StandardsError(
                "snapshot_changed",
                f"{STORE}/{insp.key} exists but its files changed. It was not replaced; "
                "restore it, or remove it and import again.",
            )
        shutil.rmtree(staging, ignore_errors=True)
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    os.replace(staging, target)
    for path in target.rglob("*"):
        if path.is_file():
            _read_only(path)
    return True


# --- references and the templates a study uses ---------------------------------------------


def _normal_repo(repo: str) -> str:
    text = repo.strip().lower().removeprefix("https://").removesuffix("/").removesuffix(".git")
    return text if text.count("/") >= 2 else f"github.com/{text}"


def resolve_ref(state: Mapping[str, Any], ref: Mapping[str, Any], type_id: str) -> tuple | None:
    """``(snapshot key, component id)`` an imported snapshot offers for ``ref``, or None.

    A bundle reference takes the highest matching version, then the latest import. A
    tool-native reference matches its pins (tree hash, commit) when it has them, else the
    same repository, subfolder and type; the latest import wins.
    """
    snapshots = state["snapshots"]
    found: list[tuple[Any, str, str]] = []
    if "bundle" in ref:
        for key, snap in snapshots.items():
            if snap["kind"] != "bundle" or snap.get("bundle_id") != ref["bundle"]:
                continue
            if not in_range(snap.get("version", "0"), ref["version"]):
                continue
            pin = ref.get("inventory_sha256")
            if pin is not None and snap["inventory_sha256"] != pin:
                continue
            comp = snap["components"].get(ref["component"])
            if comp is None or comp["type"] != type_id or comp.get("ref") is not None:
                continue
            found.append(
                ((version_tuple(snap["version"]), snap["imported_at"]), key, ref["component"])
            )
    else:
        for key, snap in snapshots.items():
            if snap["kind"] != "template" or snap.get("tool") != ref["tool"]:
                continue
            comp = snap["components"].get(TEMPLATE_COMPONENT)
            if comp is None or comp["type"] != type_id:
                continue
            source = snap["source"]
            if source.get("type") != ref.get("type"):
                continue
            if "sha256" in ref:  # the files themselves: a local copy of them counts too
                ok = snap["inventory_sha256"] == ref["sha256"]
            elif "commit" in ref:
                ok = source.get("commit") == ref["commit"]
                ok = ok and source.get("subdir") == ref.get("subdir")
            else:
                ok = _normal_repo(source.get("repo", "")) == _normal_repo(ref["repo"])
                ok = ok and source.get("subdir") == ref.get("subdir")
            if ok:
                found.append((snap["imported_at"], key, TEMPLATE_COMPONENT))
    if not found:
        return None
    found.sort(key=lambda row: row[0])
    return found[-1][1], found[-1][2]


def ref_hint(ref: Mapping[str, Any]) -> str:
    """What to import so a reference resolves, in words."""
    if "bundle" in ref:
        pin = " (with the pinned hash)" if "inventory_sha256" in ref else ""
        return f"Import the standards bundle {ref['bundle']} {ref['version']}{pin}."
    where = ref["repo"] + (f", folder {ref['subdir']}" if ref.get("subdir") else "")
    at = ref.get("commit") or ref.get("ref") or "its default branch"
    kind = f" type {ref['type']}" if ref.get("type") else ""
    return f"Import the {ref['tool']} template{kind} from {where} at {at}."


@dataclass(frozen=True)
class Material:
    """A selected template, verified against its recorded hash just now."""

    type: str
    snapshot: str
    component: str
    folder: Path
    files: dict[str, Path]  # path relative to ``folder`` -> file
    native_type: str | None
    provenance: dict[str, Any]


def provenance(state: Mapping[str, Any], selected: Mapping[str, Any]) -> dict[str, Any]:
    snap = state["snapshots"][selected["snapshot"]]
    comp = snap["components"][selected["component"]]
    out: dict[str, Any] = {
        "snapshot": selected["snapshot"],
        "component": selected["component"],
        "component_sha256": comp["sha256"],
        "kind": snap["kind"],
        "source": dict(snap["source"]),
    }
    if snap["kind"] == "bundle":
        out.update(bundle_id=snap["bundle_id"], version=snap["version"])
    else:
        out["tool"] = snap["tool"]
    if "via" in selected:
        out["via"] = dict(selected["via"])
    return out


def material(root: Path, type_id: str) -> Material | None:
    """The template the study selected for ``type_id``, re-hashed now; None if none is.

    Raises ``StandardsError("standards_changed")`` when the snapshot's files no longer match
    the hash recorded at import, so a build never uses material that changed silently.
    """
    state = read_state(root)
    selected = state["selected"].get(type_id)
    if selected is None:
        return None
    snap = state["snapshots"].get(selected["snapshot"])
    comp = snap["components"].get(selected["component"]) if snap else None
    if snap is None or comp is None or comp["sha256"] is None:
        raise StandardsError(
            "standards_changed", f"The selected {type_id} is no longer in the imported standards."
        )
    base = snapshot_dir(root, selected["snapshot"])
    if files_digest(base, comp["files"]) != comp["sha256"]:
        raise StandardsError(
            "standards_changed",
            f"The files of the selected {type_id} changed since they were imported. Import the "
            "bundle again, or choose another template.",
        )
    folder = folder_of(comp["files"]) if snap["kind"] == "bundle" else ""
    return Material(
        type=type_id,
        snapshot=selected["snapshot"],
        component=selected["component"],
        folder=base / folder if folder else base,
        files={within(folder, p): base.joinpath(*p.split("/")) for p in comp["files"]},
        native_type=snap["source"].get("type") if snap["kind"] == "template" else None,
        provenance=provenance(state, selected),
    )


# --- the service ----------------------------------------------------------------------------


@dataclass
class Preview:
    id: str
    root: Path
    staging: Path
    insp: Inspection
    source: dict[str, Any]
    created: float
    fetched: Path | None = None


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "new"


def _diff(old: bytes, new: bytes, dest: str, label: str) -> list[str] | None:
    try:
        a, b = old.decode("utf-8"), new.decode("utf-8")
    except UnicodeDecodeError:
        return None
    lines = list(
        difflib.unified_diff(
            a.splitlines(), b.splitlines(), f"{dest} (yours)", f"{dest} ({label})", lineterm=""
        )
    )
    if len(lines) > MAX_DIFF_LINES:
        lines = [*lines[:MAX_DIFF_LINES], f"… {len(lines) - MAX_DIFF_LINES} more lines"]
    return lines


class Standards:
    """Inspect, install and select standards for the open study."""

    def __init__(self, types: Iterable[StandardsType], catalog: Any) -> None:
        self.types: dict[str, StandardsType] = {}
        for kind in types:
            kind.validate()
            if kind.id in self.types:
                raise ValueError(f"Duplicate standards type {kind.id!r}.")
            self.types[kind.id] = kind
        self.catalog = catalog
        self.lock = threading.Lock()
        self.previews: OrderedDict[str, Preview] = OrderedDict()

    # --- listing ----------------------------------------------------------------------------

    def types_json(self) -> list[dict[str, Any]]:
        return [
            {
                "id": k.id,
                "title": k.title,
                "format": k.format,
                "install_to": k.install_to,
                "native": k.native,
                "selectable": k.install_to is None,
            }
            for k in self.types.values()
        ]

    def listing(self, root: Path) -> dict[str, Any]:
        state = read_state(root)
        snapshots = []
        for key, snap in sorted(state["snapshots"].items(), key=lambda kv: kv[1]["imported_at"]):
            comps = []
            for cid, comp in snap["components"].items():
                row = {"id": cid, **{k: v for k, v in comp.items() if k != "files"}}
                row["files"] = len(comp["files"])
                if comp.get("ref") is not None:
                    hit = resolve_ref(state, comp["ref"], comp["type"])
                    row["resolved"] = {"snapshot": hit[0], "component": hit[1]} if hit else None
                    row["hint"] = None if hit else ref_hint(comp["ref"])
                comps.append(row)
            snapshots.append(
                {
                    "key": key,
                    **{k: v for k, v in snap.items() if k != "components"},
                    "components": comps,
                }
            )
        selected = {}
        for type_id, chosen in state["selected"].items():
            try:
                found = material(root, type_id)
                selected[type_id] = {**chosen, "ok": found is not None, "problem": None}
            except StandardsError as exc:
                selected[type_id] = {**chosen, "ok": False, "problem": exc.message}
        return {
            "snapshots": list(reversed(snapshots)),
            "installed": state["installed"],
            "selected": selected,
        }

    # --- inspect ----------------------------------------------------------------------------

    def _expire(self, root: Path) -> None:
        moment = time.monotonic()
        for pid, prev in list(self.previews.items()):
            if moment - prev.created > PREVIEW_TTL_S or len(self.previews) >= PREVIEWS_KEPT:
                self._drop(pid)
        store = resolve_in_root(root, STORE)
        if store.is_dir():
            live = {p.staging.name for p in self.previews.values()}
            for entry in store.iterdir():
                if entry.name.startswith(STAGING_PREFIX) and entry.name not in live:
                    shutil.rmtree(entry, ignore_errors=True)
            fetched = store / FETCHED
            if fetched.is_dir():
                for entry in fetched.iterdir():
                    try:
                        old = time.time() - entry.stat().st_mtime > FETCHED_TTL_S
                    except OSError:
                        continue
                    if old:
                        shutil.rmtree(entry, ignore_errors=True)

    def _drop(self, pid: str) -> None:
        prev = self.previews.pop(pid, None)
        if prev is not None:
            shutil.rmtree(prev.staging, ignore_errors=True)

    def fetched_dir(self, root: Path, fetch_id: str) -> Path:
        if not FETCH_ID_RE.match(fetch_id):
            raise StandardsError("not_found", "There is no such fetched source.")
        return resolve_in_root(root, f"{STORE}/{FETCHED}/{fetch_id}")

    def _source(self, root: Path, source: Mapping[str, Any], scratch: Path):
        """``(folder to read, source record, fetched folder or None)`` for a request."""
        kind = source.get("kind")
        if kind == "fetched":
            folder = self.fetched_dir(root, str(source.get("id", "")))
            try:
                meta = json.loads((folder / "fetch.json").read_text("utf-8"))
            except (OSError, ValueError):
                raise StandardsError("not_found", "Fetch the source again.") from None
            record = {"kind": "git", "display": meta["display"], "repo": meta["repo"]}
            for name in ("ref", "commit", "subdir", "type"):
                if meta.get(name):
                    record[name] = meta[name]
            return folder / "files", record, folder
        if kind != "path":
            raise StandardsError("bad_source", "Choose a folder, an archive or a Git source.")
        text = str(source.get("path", "")).strip()
        if not text or "\x00" in text:
            raise StandardsError("bad_source", "Enter the folder or archive to import.")
        given = Path(text).expanduser()
        try:
            path = given.resolve() if given.is_absolute() else resolve_in_root(root, text)
        except (OSError, WorkspaceAccessError):
            raise StandardsError("bad_source", "That path can't be used.") from None
        if path.is_dir():
            return path, {"kind": "dir", "display": path.name}, None
        if path.is_file() and path.name.lower().endswith(ARCHIVE_SUFFIXES):
            skipped = extract_archive(path, scratch)
            record = {"kind": "archive", "display": path.name}
            if skipped:
                record["skipped"] = skipped
            return scratch, record, None
        raise StandardsError(
            "bad_source", "That is not a folder or a .zip, .tar, .tar.gz or .tgz archive."
        )

    def inspect(
        self,
        root: Path,
        source: Mapping[str, Any],
        template: Mapping[str, Any] | None = None,
        expect: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Read a source, verify everything, stage it, and describe what install would do."""
        with self.lock:
            self._expire(root)
            pid = secrets.token_hex(8)
            staging = resolve_in_root(root, f"{STORE}/{STAGING_PREFIX}{pid}")
            scratch = staging.with_name(staging.name + "-src")
            try:
                folder, record, fetched = self._source(root, source, scratch)
                skipped = record.pop("skipped", 0)
                archive = record["kind"] == "archive"
                if template is not None:
                    tool = template.get("tool")
                    native_type = template.get("type")
                    if tool not in STANDARDS_TOOLS:
                        raise StandardsError("bad_source", "Choose deckifyr or quartifyr.")
                    if native_type is not None and not NAME_RE.match(str(native_type)):
                        raise StandardsError("bad_source", "A template type is a plain name.")
                    base = top_folder(folder, TEMPLATE_MARKERS) if archive else folder
                    if native_type is not None and not (base / "templates" / native_type).is_dir():
                        raise StandardsError(
                            "bad_source", f"The source has no templates/{native_type}/ folder."
                        )
                    insp = inspect_template(base, tool, native_type, self.types, self.catalog)
                    if native_type is not None:
                        record["type"] = native_type
                else:
                    base = top_folder(folder, (MANIFEST,)) if archive else folder
                    insp = inspect_bundle(base, self.types, self.catalog)
                if skipped:
                    insp.notes.append(
                        f"{skipped} archive entries (links or hidden files) were left out"
                    )
                if expect:
                    if (
                        expect.get("sha256")
                        and insp.problems == []
                        and insp.inventory_sha256 != expect["sha256"]
                    ):
                        insp.problems.append("the files don't match the SHA-256 the reference pins")
                    if expect.get("commit") and record.get("commit") != expect["commit"]:
                        insp.problems.append("the commit doesn't match the one the reference pins")
                if insp.problems:
                    insp.key = ""
                else:
                    stage(staging, insp.data)
            finally:
                shutil.rmtree(scratch, ignore_errors=True)
            state = read_state(root)
            preview = None
            if not insp.problems:
                preview = Preview(pid, root, staging, insp, record, time.monotonic(), fetched)
                self.previews[pid] = preview
            return self._describe(root, state, insp, record, preview)

    def _plan(
        self, root: Path, state: Mapping[str, Any], insp: Inspection, comp: Component
    ) -> list[dict]:
        kind = self.types[comp.type]
        rows = []
        if kind.install_to is None or comp.ref is not None:
            return rows
        label = f"{insp.doc['id']} {insp.doc['version']}" if insp.doc else insp.title
        for path, sha in sorted(comp.files.items()):
            name = PurePosixPath(path).name
            dest = f"{kind.install_to}/{name}"
            target = resolve_in_root(root, dest)
            row: dict[str, Any] = {"source": path, "dest": dest, "sha256": sha}
            if not target.exists():
                row["status"] = "new"
            else:
                current = (
                    target.read_bytes() if target.is_file() and not target.is_symlink() else b""
                )
                have = sha256_bytes(current)
                before = state["installed"].get(dest)
                if have == sha:
                    row["status"] = "unchanged"
                elif before is not None and before["sha256"] == have:
                    row["status"] = "update"
                else:
                    row["status"] = "conflict"
                    row["reason"] = "edited" if before is not None else "not_from_a_bundle"
                    row["copy_to"] = self._copy_name(root, kind.install_to, name, insp, sha)
                    row["diff"] = _diff(current, insp.data[path], dest, label)
            rows.append(row)
        return rows

    def _copy_name(self, root: Path, folder: str, name: str, insp: Inspection, sha: str) -> str:
        stem = name.removesuffix(".yaml")
        tag = _slug(insp.doc["version"] if insp.doc else insp.inventory_sha256[:8])
        for n in range(1, 100):
            suffix = f"-{tag}" if n == 1 else f"-{tag}-{n}"
            candidate = f"{stem[: 64 - len(suffix)]}{suffix}.yaml"
            target = resolve_in_root(root, f"{folder}/{candidate}")
            if not target.exists() or (
                target.is_file() and sha256_bytes(target.read_bytes()) == sha
            ):
                return f"{folder}/{candidate}"
        raise StandardsError("exists", f"No free name for a copy of {folder}/{name}.")

    def _describe(
        self,
        root: Path,
        state: Mapping[str, Any],
        insp: Inspection,
        record: Mapping[str, Any],
        preview: Preview | None,
    ) -> dict[str, Any]:
        comps = []
        for comp in insp.components.values():
            kind = self.types.get(comp.type)
            row: dict[str, Any] = {
                "id": comp.id,
                "type": comp.type,
                "type_title": kind.title if kind else comp.type,
                "title": comp.title,
                "description": comp.description,
                "sha256": comp.sha256,
                "links": comp.links,
                "install_to": kind.install_to if kind else None,
                "selectable": bool(kind and kind.install_to is None),
                "files": [
                    {"path": p, "sha256": s, "bytes": len(insp.data.get(p, b""))}
                    for p, s in sorted(comp.files.items())
                ],
            }
            if comp.ref is not None:
                hit = resolve_ref(state, comp.ref, comp.type)
                row["ref"] = comp.ref
                row["resolved"] = {"snapshot": hit[0], "component": hit[1]} if hit else None
                row["hint"] = None if hit else ref_hint(comp.ref)
            if kind is not None and not insp.problems:
                row["plan"] = self._plan(root, state, insp, comp)
            comps.append(row)
        out: dict[str, Any] = {
            "preview_id": preview.id if preview else None,
            "kind": insp.kind,
            "title": insp.title,
            "source": dict(record),
            "snapshot": insp.key or None,
            "already_imported": bool(insp.key) and insp.key in state["snapshots"],
            "inventory_sha256": insp.inventory_sha256 if insp.data else None,
            "compatibility": insp.compatibility,
            "problems": insp.problems,
            "notes": insp.notes,
            "components": comps,
            "variants": (insp.doc or {}).get("variants", []),
            "manifest": MANIFEST if insp.kind == "bundle" else None,
        }
        if insp.doc:
            out["bundle"] = {
                "id": insp.doc["id"],
                "version": insp.doc["version"],
                "title": insp.doc["title"],
                "description": insp.doc.get("description"),
            }
        else:
            out["template"] = {"tool": insp.tool, "type": insp.native_type}
        return out

    # --- install ----------------------------------------------------------------------------

    def _chosen(
        self, insp: Inspection, components: list[str] | None, variant: str | None
    ) -> list[str]:
        if insp.kind == "template":
            return [TEMPLATE_COMPONENT]
        if variant is not None:
            match = [v for v in insp.doc.get("variants", []) if v["id"] == variant]
            if not match:
                raise StandardsError("not_found", "The bundle has no such variant.")
            wanted = list(match[0]["components"])
        elif components:
            wanted = list(components)
        else:
            wanted = list(insp.components)
        unknown = [c for c in wanted if c not in insp.components]
        if unknown:
            raise StandardsError("not_found", f"The bundle has no component {unknown[0]}.")
        chosen: list[str] = []
        queue = list(wanted)
        while queue:  # a component brings the components it links to
            cid = queue.pop(0)
            if cid not in chosen:
                chosen.append(cid)
                queue.extend(insp.components[cid].links)
        return chosen

    def install(
        self,
        root: Path,
        preview_id: str,
        components: list[str] | None = None,
        variant: str | None = None,
        choices: Mapping[str, str] | None = None,
        select: Iterable[str] = (),
    ) -> dict[str, Any]:
        """Apply a preview: keep the snapshot, copy files, record it all, select templates.

        Every check is repeated against the study as it is now. A file that changed since
        the preview stops the install before anything is written.
        """
        choices = dict(choices or {})
        with self.lock:
            prev = self.previews.get(preview_id)
            if prev is None or prev.root != root or time.monotonic() - prev.created > PREVIEW_TTL_S:
                raise StandardsError("preview_expired", "Inspect the source again, then install.")
            insp = prev.insp
            chosen = self._chosen(insp, components, variant)
            state = read_state(root)
            plans: list[tuple[Component, dict[str, Any]]] = []
            conflicts = []
            for cid in chosen:
                comp = insp.components[cid]
                for row in self._plan(root, state, insp, comp):
                    plans.append((comp, row))
                    if row["status"] == "conflict" and choices.get(row["dest"]) not in (
                        "keep",
                        "copy",
                    ):
                        conflicts.append(row["dest"])
            if conflicts:
                raise StandardsError(
                    "needs_choice",
                    f"{', '.join(conflicts)} differ from the bundle's version and weren't "
                    "copied by an earlier import, or were edited since. Keep yours, or install "
                    "the bundle's version beside it.",
                    conflicts=conflicts,
                    options=["keep", "copy"],
                )
            selections = self._selections(state, insp, chosen, select)
            if files_digest(prev.staging, insp.data) != insp.inventory_sha256:
                self._drop(preview_id)
                raise StandardsError("preview_expired", "The staged files changed. Inspect again.")
            created = promote(root, prev.staging, insp)
            written: list[dict[str, str]] = []
            for comp, row in plans:
                dest = row["dest"]
                if row["status"] == "unchanged":
                    state["installed"].setdefault(
                        dest, {"sha256": row["sha256"], "snapshot": insp.key, "component": comp.id}
                    )
                    continue
                if row["status"] == "conflict":
                    if choices[dest] == "keep":
                        written.append({"path": dest, "action": "kept"})
                        continue
                    dest = row["copy_to"]
                atomic_write_bytes(resolve_in_root(root, dest), insp.data[row["source"]])
                state["installed"][dest] = {
                    "sha256": row["sha256"],
                    "snapshot": insp.key,
                    "component": comp.id,
                }
                written.append(
                    {"path": dest, "action": row["status"] if dest == row["dest"] else "copy"}
                )
            state["snapshots"][insp.key] = self._record(insp, prev.source, state)
            for type_id, chosen_sel in selections.items():
                state["selected"][type_id] = {**chosen_sel, "selected_at": now()}
            write_state(root, state)
            self.previews.pop(preview_id, None)
            if prev.fetched is not None:
                shutil.rmtree(prev.fetched, ignore_errors=True)
            return {
                "snapshot": insp.key,
                "created": created,
                "files": written,
                "selected": sorted(selections),
            }

    def _record(
        self, insp: Inspection, source: Mapping[str, Any], state: Mapping[str, Any]
    ) -> dict[str, Any]:
        old = state["snapshots"].get(insp.key)
        record: dict[str, Any] = {
            "kind": insp.kind,
            "title": insp.title,
            "inventory_sha256": insp.inventory_sha256,
            "source": dict(old["source"] if old else source),
            "imported_at": old["imported_at"] if old else now(),
            "components": {
                c.id: {
                    "type": c.type,
                    "title": c.title,
                    "sha256": c.sha256,
                    "files": sorted(c.files),
                    "links": c.links,
                    **({"ref": c.ref} if c.ref is not None else {}),
                }
                for c in insp.components.values()
            },
        }
        if insp.doc:
            record.update(bundle_id=insp.doc["id"], version=insp.doc["version"])
        else:
            record["tool"] = insp.tool
        return record

    def _selections(
        self, state: Mapping[str, Any], insp: Inspection, chosen: list[str], select: Iterable[str]
    ) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        future = {
            **state["snapshots"],
            insp.key: self._record(insp, {"kind": "dir", "display": ""}, state),
        }
        view = {**state, "snapshots": future}
        for cid in select:
            if cid not in chosen:
                raise StandardsError("not_found", f"{cid} is not among the components installed.")
            comp = insp.components[cid]
            kind = self.types[comp.type]
            if kind.install_to is not None:
                raise StandardsError("not_selectable", f"{cid} is copied, not selected.")
            if comp.type in out:
                raise StandardsError("not_selectable", f"Choose one {kind.title} at a time.")
            out[comp.type] = self._target(view, insp.key, cid)
        return out

    def _target(self, state: Mapping[str, Any], key: str, cid: str) -> dict[str, Any]:
        snap = state["snapshots"][key]
        comp = snap["components"][cid]
        if comp.get("ref") is None:
            return {"snapshot": key, "component": cid}
        hit = resolve_ref(state, comp["ref"], comp["type"])
        if hit is None:
            raise StandardsError(
                "unresolved",
                f"{cid} references a template that isn't imported. {ref_hint(comp['ref'])}",
            )
        return {"snapshot": hit[0], "component": hit[1], "via": {"snapshot": key, "component": cid}}

    # --- select -----------------------------------------------------------------------------

    def select(
        self, root: Path, type_id: str, key: str | None, component: str | None
    ) -> dict[str, Any]:
        """Use an imported template for new report or deck setups (None: the bundled one)."""
        kind = self.types.get(type_id)
        if kind is None or kind.install_to is not None:
            raise StandardsError("not_selectable", "Only a report or deck template is selected.")
        with self.lock:
            state = read_state(root)
            if key is None:
                state["selected"].pop(type_id, None)
            else:
                snap = state["snapshots"].get(key)
                comp = snap["components"].get(component or "") if snap else None
                if comp is None or comp["type"] != type_id:
                    raise StandardsError("not_found", f"There is no such {kind.title}.")
                target = self._target(state, key, component or "")
                state["selected"][type_id] = {**target, "selected_at": now()}
            write_state(root, state)
        if key is not None:
            material(root, type_id)  # refuse now, not at setup, if it changed
        return self.listing(root)
