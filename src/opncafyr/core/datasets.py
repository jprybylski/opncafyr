"""Datasets in a study workspace: shallow scan and read-only preview (#21).

Core knows only the shape of a preview, not any file format or science. Formats come
from importers and extra facts (such as candidate analytes) from annotators; both are
supplied by workflow packs (``core.registry.load_workflows``), so a new format plugs in
without changing core.

Rules:
- The scan is shallow: files directly in the workspace root and directly in
  ``source-data/``. Nothing deeper and nothing under a dot-folder (``.opncafyr/``) is
  listed or read.
- Every path goes through ``resolve_in_root``. A preview may only name a file the scan
  could list.
- Source files are opened read-only (``rb``) and never locked or written. Size and
  SHA-256 are reported so a later import can record the exact bytes.
- Cell values never reach a log. This module logs nothing about file contents.
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
from collections import OrderedDict
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from opncafyr.core.workspace import WorkspaceAccessError, resolve_in_root

log = logging.getLogger(__name__)

SOURCE_DATA_DIR = "source-data"
SCAN_FOLDERS = (".", SOURCE_DATA_DIR)
DEFAULT_HEAD_ROWS = 50
MAX_HEAD_ROWS = 200
# Files up to this size are read in full; larger ones are sampled (and labelled so).
FULL_SCAN_MAX_BYTES = 50_000_000
DEFAULT_SAMPLE_ROWS = 100_000
# Distinct raw values kept per column; beyond this a column's values are not listed.
VALUE_CAP = 50
MAX_MISFITS = 3
CACHE_SIZE = 32
HASH_CHUNK = 1 << 20


class DatasetError(ValueError):
    """A dataset can't be listed or read. ``message`` is written for a person."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class ValueCount:
    value: str
    count: int


@dataclass
class ColumnProfile:
    """One column as read: inferred type, missing count and its distinct raw values."""

    name: str
    type: str  # integer, double, logical, date, datetime or character
    n_values: int  # non-missing values
    n_missing: int
    format: str | None = None  # strptime format of a date or datetime column
    minimum: float | str | None = None
    maximum: float | str | None = None
    # A character column that a narrower type fits for at least half its values:
    # that type and up to MAX_MISFITS values that don't fit it.
    near_type: str | None = None
    misfits: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    # Distinct raw (untrimmed) values with counts, most common first. Empty and
    # ``values_capped`` when the column has more than VALUE_CAP distinct values.
    values: list[ValueCount] = field(default_factory=list)
    values_capped: bool = False


@dataclass
class DatasetPreview:
    format: str  # importer id
    size_bytes: int
    sha256: str
    columns: list[ColumnProfile]
    row_count: int
    row_count_exact: bool
    rows_read: int
    head: list[list[str]]  # first rows, raw
    details: dict[str, Any] = field(default_factory=dict)  # format facts (delimiter, ...)
    warnings: list[str] = field(default_factory=list)
    annotations: dict[str, Any] = field(default_factory=dict)
    path: str = ""  # relative to the workspace root, set by the service
    modified: str = ""

    @property
    def sampled(self) -> bool:
        return not self.row_count_exact

    def to_dict(self, head_rows: int = DEFAULT_HEAD_ROWS) -> dict[str, Any]:
        doc = asdict(self)
        doc["head"] = doc["head"][: max(0, head_rows)]
        doc["sampled"] = self.sampled
        return doc


@runtime_checkable
class Importer(Protocol):
    """Reads one file format into a ``DatasetPreview`` without modifying the file."""

    id: str
    label: str
    extensions: tuple[str, ...]  # lower case, with the dot

    def read(
        self,
        path: Path,
        *,
        head_rows: int,
        full_scan_max_bytes: int,
        sample_rows: int,
    ) -> DatasetPreview: ...


@runtime_checkable
class DatasetAnnotator(Protocol):
    """Adds workflow facts to a preview; the result goes under ``annotations[id]``."""

    id: str

    def annotate(self, preview: DatasetPreview) -> dict[str, Any]: ...


def fingerprint(path: Path) -> tuple[int, str]:
    """Size and SHA-256 of a file, read in chunks, read-only."""
    digest = hashlib.sha256()
    size = 0
    with Path(path).open("rb") as fh:
        while chunk := fh.read(HASH_CHUNK):
            digest.update(chunk)
            size += len(chunk)
    return size, digest.hexdigest()


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class DatasetService:
    """Scan, preview and locate datasets with the installed importers."""

    def __init__(
        self,
        importers: Iterable[Importer],
        annotators: Iterable[DatasetAnnotator] = (),
        *,
        full_scan_max_bytes: int = FULL_SCAN_MAX_BYTES,
        sample_rows: int = DEFAULT_SAMPLE_ROWS,
    ) -> None:
        self._by_ext: dict[str, Importer] = {}
        for importer in importers:
            for ext in importer.extensions:
                self._by_ext.setdefault(ext.lower(), importer)
        self.annotators: tuple[DatasetAnnotator, ...] = tuple(annotators)
        self.full_scan_max_bytes = full_scan_max_bytes
        self.sample_rows = sample_rows
        self._cache: OrderedDict[tuple[str, int, int], DatasetPreview] = OrderedDict()
        self._lock = threading.Lock()

    @property
    def formats(self) -> list[dict[str, Any]]:
        seen: dict[str, Importer] = {}
        for importer in self._by_ext.values():
            seen.setdefault(importer.id, importer)
        return [
            {"id": i.id, "label": i.label, "extensions": list(i.extensions)} for i in seen.values()
        ]

    # --- locating files ---------------------------------------------------------------

    @staticmethod
    def _allowed_parents(root: Path) -> set[Path]:
        parents = {root}
        try:
            source = resolve_in_root(root, SOURCE_DATA_DIR)
        except WorkspaceAccessError:
            return parents
        if source.is_dir():
            parents.add(source)
        return parents

    def locate(self, root: Path, requested: str) -> tuple[Path, str, Importer]:
        """The canonical path, workspace-relative path and importer for ``requested``."""
        root = Path(root).resolve(strict=True)
        try:
            path = resolve_in_root(root, requested)
        except WorkspaceAccessError as exc:
            raise DatasetError("invalid_path", str(exc)) from None
        if path.parent not in self._allowed_parents(root) or any(
            part.startswith(".") for part in path.relative_to(root).parts
        ):
            raise DatasetError(
                "not_allowed",
                f"Only files directly in the study folder or in {SOURCE_DATA_DIR}/ can be "
                "previewed.",
            )
        importer = self._by_ext.get(path.suffix.lower())
        if importer is None:
            raise DatasetError("unsupported", "This file type can't be imported.")
        if not path.is_file():
            raise DatasetError("not_found", "This file does not exist.")
        return path, path.relative_to(root).as_posix(), importer

    def candidates(self, root: Path) -> list[tuple[Path, str, Importer]]:
        """Importable files in the scan folders, without reading any of them."""
        root = Path(root).resolve(strict=True)
        found: dict[Path, tuple[Path, str, Importer]] = {}
        for folder in SCAN_FOLDERS:
            directory = root if folder == "." else root / folder
            try:
                entries = sorted(os.scandir(directory), key=lambda e: e.name.lower())
            except OSError:
                continue
            for entry in entries:
                if entry.name.startswith("."):
                    continue
                if entry.name[entry.name.rfind(".") :].lower() not in self._by_ext:
                    continue
                rel = entry.name if folder == "." else f"{folder}/{entry.name}"
                try:
                    path, rel_path, importer = self.locate(root, rel)
                except DatasetError:
                    continue
                found.setdefault(path, (path, rel_path, importer))
        return list(found.values())

    # --- reading ------------------------------------------------------------------------

    def _load(self, path: Path, rel: str, importer: Importer) -> DatasetPreview:
        try:
            before = path.stat()
        except OSError:
            raise DatasetError("not_found", "This file can't be opened.") from None
        key = (str(path), before.st_mtime_ns, before.st_size)
        with self._lock:
            cached = self._cache.get(key)
            if cached is not None:
                self._cache.move_to_end(key)
                cached.path = rel
                return cached
        try:
            preview = importer.read(
                path,
                head_rows=MAX_HEAD_ROWS,
                full_scan_max_bytes=self.full_scan_max_bytes,
                sample_rows=self.sample_rows,
            )
        except DatasetError:
            raise
        except PermissionError:
            raise DatasetError(
                "unreadable", "You don't have permission to read this file."
            ) from None
        except OSError:
            raise DatasetError("unreadable", "This file could not be read.") from None
        preview.path = rel
        preview.modified = _iso(before.st_mtime)
        for annotator in self.annotators:
            try:
                preview.annotations[annotator.id] = annotator.annotate(preview)
            except Exception as exc:  # noqa: BLE001 - one annotator must not break a preview
                log.warning("Dataset annotator %s failed (%s)", annotator.id, type(exc).__name__)
                preview.annotations[annotator.id] = {"error": "These suggestions failed."}
        try:
            after = path.stat()
        except OSError:
            after = None
        if after is None or (after.st_mtime_ns, after.st_size) != key[1:]:
            preview.warnings.append(
                "The file changed while it was being read. Preview it again before importing."
            )
            return preview
        with self._lock:
            self._cache[key] = preview
            while len(self._cache) > CACHE_SIZE:
                self._cache.popitem(last=False)
        return preview

    def preview(self, root: Path, requested: str, rows: int = DEFAULT_HEAD_ROWS) -> dict[str, Any]:
        path, rel, importer = self.locate(root, requested)
        return self._load(path, rel, importer).to_dict(min(rows, MAX_HEAD_ROWS))

    def scan(self, root: Path) -> list[dict[str, Any]]:
        """One summary per importable file in the scan folders (no data rows)."""
        files: list[dict[str, Any]] = []
        for path, rel, importer in self.candidates(root):
            folder = rel.rpartition("/")[0] or "."
            entry: dict[str, Any] = {"path": rel, "name": path.name, "folder": folder}
            entry["format"] = importer.id
            try:
                p = self._load(path, rel, importer)
            except DatasetError as exc:
                try:
                    stat = path.stat()
                    entry |= {"size_bytes": stat.st_size, "modified": _iso(stat.st_mtime)}
                except OSError:
                    pass
                entry["error"] = {"code": exc.code, "message": exc.message}
                files.append(entry)
                continue
            entry |= {
                "size_bytes": p.size_bytes,
                "modified": p.modified,
                "sha256": p.sha256,
                "columns": [c.name for c in p.columns],
                "row_count": p.row_count,
                "row_count_exact": p.row_count_exact,
                "sampled": p.sampled,
                "warnings": len(p.warnings),
                "annotations": p.annotations,
            }
            files.append(entry)
        return files

    def clear_cache(self) -> None:
        with self._lock:
            self._cache.clear()
