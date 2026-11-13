"""reportifyr metadata sidecars: names, hashes and checks (#30).

reportifyr reads ``<stem>_<ext>_metadata.json`` next to each table or figure in
``OUTPUTS/``. When a sidecar is missing or malformed, reportifyr only logs it, and the
report still "succeeds". The launcher therefore checks every sidecar before a publish,
render or build. We do not change reportifyr (docs/plan.md decision log).

The format is reportifyr 0.4.0's (schema ``reportifyr-sidecar-v1``):
- ``object_meta.path`` is relative to the project root (the folder that holds ``OUTPUTS/``);
- ``object_meta.hash`` is BLAKE3 over the file's bytes (``digest::digest(algo="blake3")``);
- ``meta_type`` is ``"NA"`` or a key of ``figure_footnotes``/``table_footnotes`` in
  ``standard_footnotes.yaml``, chosen by file type;
- every abbreviation must be a key of its ``abbreviations``.

Findings name files and checks, never data values. The check ids follow quartifyr's
``check_report_project()`` where they mean the same thing.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

import blake3
import yaml
from jsonschema import Draft202012Validator

from opncafyr.core.manifest import load_schema
from opncafyr.core.workspace import WorkspaceAccessError, resolve_in_root

OUTPUTS = "OUTPUTS"
SUFFIX = "_metadata.json"
SCHEMA = "reportifyr-sidecar-v1"
# What reportifyr and quartifyr treat as a figure; every other file is a table.
FIGURE_EXTENSIONS = frozenset(
    {"png", "jpg", "jpeg", "gif", "bmp", "tif", "tiff", "svg", "emf", "wmf"}
)
NO_META_TYPE = "NA"
MAX_SIDECAR_BYTES = 1 << 20
MAX_FOOTNOTES_BYTES = 4 << 20


class SidecarError(ValueError):
    """A footnotes file that can't be used to check sidecars."""


@dataclass(frozen=True)
class Finding:
    check: str
    severity: str  # "error" | "warning"
    path: str | None
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "check": self.check,
            "severity": self.severity,
            "path": self.path,
            "message": self.message,
        }


@dataclass(frozen=True)
class Footnotes:
    abbreviations: frozenset[str]
    figure_types: frozenset[str]
    table_types: frozenset[str]


def load_footnotes(path: Path | Any) -> Footnotes:
    """The keys of a ``standard_footnotes.yaml`` (a path or a resources Traversable)."""
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise SidecarError(f"The footnotes file can't be read: {exc.strerror}.") from None
    return parse_footnotes(raw)


def parse_footnotes(raw: bytes) -> Footnotes:
    """The keys of a ``standard_footnotes.yaml`` text."""
    if len(raw) > MAX_FOOTNOTES_BYTES:
        raise SidecarError("The footnotes file is too large.")
    try:
        doc = yaml.safe_load(raw.decode("utf-8"))
    except (UnicodeDecodeError, yaml.YAMLError):
        raise SidecarError("The footnotes file is not valid UTF-8 YAML.") from None
    if not isinstance(doc, dict):
        raise SidecarError("The footnotes file must be a YAML mapping.")

    def keys(name: str) -> frozenset[str]:
        section = doc.get(name) or {}
        if not isinstance(section, dict):
            raise SidecarError(f"'{name}' in the footnotes file must be a mapping.")
        return frozenset(str(k) for k in section)

    return Footnotes(keys("abbreviations"), keys("figure_footnotes"), keys("table_footnotes"))


def sidecar_name(name: str) -> str:
    """``conc-time.png`` -> ``conc-time_png_metadata.json`` (reportifyr's naming)."""
    stem, dot, ext = name.rpartition(".")
    if not dot or not stem:
        raise ValueError(f"{name!r} has no file extension")
    return f"{stem}_{ext}{SUFFIX}"


def extension(name: str) -> str:
    return name.rpartition(".")[2] if "." in name else ""


def is_figure(name: str) -> bool:
    return extension(name).lower() in FIGURE_EXTENSIONS


def blake3_file(path: Path) -> str:
    digest = blake3.blake3()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validator() -> Draft202012Validator:
    return Draft202012Validator(load_schema(SCHEMA))


def _read_sidecar(path: Path, rel: str) -> tuple[dict[str, Any] | None, Finding | None]:
    def bad(why: str) -> tuple[None, Finding]:
        return None, Finding("invalid-sidecar", "error", rel, f"The sidecar {why}.")

    if path.is_symlink() or not path.is_file():
        return bad("is not a regular file")
    if path.stat().st_size > MAX_SIDECAR_BYTES:
        return bad("is larger than 1 MB")
    try:
        doc = json.loads(path.read_bytes().decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return bad("is not valid UTF-8 JSON")
    errors = sorted(_validator().iter_errors(doc), key=lambda e: list(e.absolute_path))
    if errors:
        first = errors[0]
        where = "/".join(str(p) for p in first.absolute_path) or "the top level"
        return bad(f"does not match reportifyr's format at {where} ({first.validator})")
    return doc, None


def check_artifact(root: Path, rel: str, footnotes: Footnotes) -> list[Finding]:
    """Every finding for one artifact, ``rel`` being e.g. ``OUTPUTS/figures/x.png``."""
    name = PurePosixPath(rel).name
    try:
        path = resolve_in_root(root, rel)
        side_rel = str(PurePosixPath(rel).parent / sidecar_name(name))
        side = resolve_in_root(root, side_rel)
    except (WorkspaceAccessError, ValueError):
        return [Finding("missing-artifact", "error", rel, "This is not a file under OUTPUTS/.")]
    # resolve_in_root follows links; published files and sidecars must be plain files.
    if (root / rel).is_symlink() or not path.is_file():
        return [Finding("missing-artifact", "error", rel, "The file is missing or a link.")]
    if (root / side_rel).is_symlink():
        return [Finding("invalid-sidecar", "error", side_rel, "The sidecar is a link.")]
    if not side.exists():
        return [
            Finding(
                "missing-sidecar",
                "error",
                rel,
                f"There is no metadata sidecar ({sidecar_name(name)}), so its footnotes "
                "would be missing.",
            )
        ]
    doc, problem = _read_sidecar(side, side_rel)
    if problem:
        return [problem]
    meta = doc["object_meta"]
    out: list[Finding] = []
    if meta["path"] != rel:
        out.append(
            Finding(
                "path-mismatch",
                "error",
                side_rel,
                f"The sidecar describes {meta['path']}, not {rel}.",
            )
        )
    if meta["file_type"] != extension(name):
        out.append(
            Finding(
                "file-type",
                "error",
                side_rel,
                f"The sidecar says file type '{meta['file_type']}'; the file is "
                f"'{extension(name)}'.",
            )
        )
    if meta["hash"] != blake3_file(path):
        out.append(
            Finding(
                "hash-mismatch",
                "error",
                side_rel,
                "The file's BLAKE3 hash doesn't match its sidecar: the file changed after "
                "its metadata was written.",
            )
        )
    known = footnotes.figure_types if is_figure(name) else footnotes.table_types
    if meta["meta_type"] != NO_META_TYPE and meta["meta_type"] not in known:
        kind = "figure" if is_figure(name) else "table"
        out.append(
            Finding(
                "unknown-meta-type",
                "error",
                side_rel,
                f"meta_type '{meta['meta_type']}' is not a {kind} footnote type in "
                "standard_footnotes.yaml.",
            )
        )
    for abbrev in meta["footnotes"]["abbreviations"]:
        if abbrev not in footnotes.abbreviations:
            out.append(
                Finding(
                    "unknown-abbreviation",
                    "error",
                    side_rel,
                    f"Abbreviation '{abbrev}' is not defined in standard_footnotes.yaml.",
                )
            )
    return out


def discover(root: Path) -> tuple[list[str], list[str]]:
    """The artifacts under ``root/OUTPUTS`` and the sidecars that have no artifact.

    Hidden files (``.x.tmp`` from an interrupted copy) and links are skipped.
    """
    base = root / OUTPUTS
    if not base.is_dir() or base.is_symlink():
        return [], []
    files: list[str] = []
    sidecars: set[str] = set()
    for path in sorted(base.rglob("*")):
        rel = path.relative_to(root).as_posix()
        if any(part.startswith(".") for part in PurePosixPath(rel).parts):
            continue
        if path.is_symlink() or not path.is_file():
            continue
        if rel.endswith(SUFFIX):
            sidecars.add(rel)
        else:
            files.append(rel)
    expected = {str(PurePosixPath(f).parent / sidecar_name(PurePosixPath(f).name)) for f in files}
    return files, sorted(sidecars - expected)


def check_outputs(
    root: Path, footnotes: Footnotes, files: Iterable[str] | None = None
) -> tuple[list[str], list[Finding]]:
    """Check ``files`` (or every artifact found under ``OUTPUTS/``); returns both."""
    findings: list[Finding] = []
    if files is None:
        listed, orphans = discover(root)
        findings += [
            Finding(
                "orphan-sidecar",
                "warning",
                rel,
                "This sidecar belongs to no file in OUTPUTS/.",
            )
            for rel in orphans
        ]
    else:
        listed = list(files)
    for rel in listed:
        findings += check_artifact(root, rel, footnotes)
    return listed, findings


def preflight(findings: Iterable[Finding]) -> dict[str, Any]:
    """A ``preflight-v1`` document."""
    return {"schema_version": 1, "findings": [f.to_dict() for f in findings]}


def blocking(findings: Iterable[Finding]) -> bool:
    return any(f.severity == "error" for f in findings)
