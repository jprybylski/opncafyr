"""The publication curator: copy approved artifacts of completed runs into ``OUTPUTS/`` (#31).

Publishing is a separate, reviewable action (docs/spec.md). The user picks tables and
figures from one or more **completed** runs. Only runs whose replay passed and whose sealed
files still match their hashes are eligible. Each pick is **copied** to
``OUTPUTS/<tables|figures>/<analyte>_<run-id>_<artifact>.<ext>`` with a reportifyr
sidecar, and ``.opncafyr/publications/<id>.json`` (``publication-v1``) records exact run
IDs and every hash.

The launcher writes the sidecars itself, in Python, rather than through reportifyr's R
``write_*_with_metadata()`` (docs/publishing.md, "Why there are no R scripts").

What can't go wrong:
- A run is never changed; files are copied, never moved or linked.
- A published file is never replaced. A name that already holds different bytes refuses
  the whole publication before anything is written. Identical bytes are reused, along with
  their sidecar when it is still valid.
- The manifest is written last. A crash part-way leaves files that no publication lists,
  which reports and decks never use. The Publish page reports them as not recorded.
"""

from __future__ import annotations

import getpass
import json
import os
import re
import secrets
import shutil
import sys
import tempfile
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from opncafyr import __version__
from opncafyr.core import runs as runs_core
from opncafyr.core.artifacts import PUBLICATIONS_DIR, list_runs
from opncafyr.core.jobs import JobState
from opncafyr.core.manifest import atomic_write_json, validate
from opncafyr.core.workspace import resolve_in_root
from opncafyr.workflows.nca.publish import sidecars
from opncafyr.workflows.nca.publish.catalog import FORMATTED, Entry, catalog, missing

SCHEMA = "publication-v1"
SIDECAR_FORMAT = "reportifyr-0.4.0"
PUBLICATION_ID_RE = re.compile(r"^pub-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{4}$")
MAX_ITEMS = 200
# Abbreviations each kind of artifact uses; kept only when the footnotes define them.
_MEAN_ABBREVIATIONS = ("PK", "BLQ", "SD", "SE")
ABBREVIATIONS = {
    "summary": ("NCA", "PK", "SD", "CV", "GM"),
    **{a: _MEAN_ABBREVIATIONS for a in ("mean-linear", "mean-log", "mean-profiles")},
}
DEFAULT_ABBREVIATIONS = ("NCA", "PK")

_lock = threading.Lock()


class PublishError(Exception):
    def __init__(self, code: str, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.extra = extra


@dataclass(frozen=True)
class Pick:
    run_id: str
    artifact: str


@dataclass
class Planned:
    run_id: str
    entry: Entry
    published_path: str
    sidecar_path: str
    status: str  # "new" | "unchanged" | "conflict"
    sidecar_bytes: bytes = b""
    reuse_sidecar: bool = False  # an identical copy's valid sidecar is kept as it is

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "artifact": self.entry.artifact,
            "title": self.entry.title,
            "kind": self.entry.kind,
            "artifact_path": self.entry.path,
            "artifact_sha256": self.entry.sha256,
            "published_path": self.published_path,
            "sidecar_path": self.sidecar_path,
            "contains_subject_ids": self.entry.contains_subject_ids,
            "status": self.status,
        }


@dataclass
class Plan:
    items: list[Planned]
    runs: dict[str, tuple[Path, dict[str, Any]]]
    findings: list[sidecars.Finding] = field(default_factory=list)

    @property
    def conflicts(self) -> list[Planned]:
        return [p for p in self.items if p.status == "conflict"]

    def to_dict(self) -> dict[str, Any]:
        return {
            "items": [p.to_dict() for p in self.items],
            "conflicts": len(self.conflicts),
            "blocking": bool(self.conflicts) or sidecars.blocking(self.findings),
            "report": sidecars.preflight(self.findings),
        }


# --- eligibility ---------------------------------------------------------------------------


def eligibility(folder: Path, manifest: dict[str, Any]) -> str | None:
    """Why a run can't be published, or None if it can."""
    if manifest["state"] != JobState.COMPLETED:
        return f"Only completed runs can be published; this one is {manifest['state']}."
    if (manifest.get("replay") or {}).get("status") != "passed":
        return "The run's replay didn't pass."
    report = runs_core.verify_run(folder)
    if not report["ok"]:
        return "The run's files no longer match their recorded hashes (Check files)."
    return None


def load_run(root: Path, run_id: str) -> tuple[Path, dict[str, Any]]:
    try:
        folder = runs_core.run_dir(root, run_id)
        manifest = runs_core.read_manifest(folder)
    except runs_core.RunError as exc:
        code = "not_found" if exc.code == "not_found" else "run_invalid"
        raise PublishError(code, exc.message) from None
    if why := eligibility(folder, manifest):
        raise PublishError("not_publishable", f"Run {run_id}: {why}", run_id=run_id)
    return folder, manifest


def publishable_runs(root: Path) -> list[dict[str, Any]]:
    """Every run, with whether it can be published and, if so, what it offers."""
    out = []
    for listed in list_runs(root):
        row: dict[str, Any] = {k: listed.get(k) for k in ("run_id", "created_at", "state")}
        row["analyte"] = listed.get("analyte")
        try:
            folder = runs_core.run_dir(root, listed["run_id"])
            manifest = runs_core.read_manifest(folder)
        except runs_core.RunError as exc:
            out.append({**row, "publishable": False, "reason": exc.message, "artifacts": []})
            continue
        why = eligibility(folder, manifest)
        offered = catalog(manifest) if why is None else []
        if why is None and not offered:
            why = "The run saved no tables or figures to publish."
        row["replay"] = (manifest.get("replay") or {}).get("status")
        row["manifest_sha256"] = runs_core.sha256_file(folder / runs_core.MANIFEST)
        row["publishable"] = why is None
        row["reason"] = why
        row["artifacts"] = [e.to_dict() for e in offered]
        row["missing"] = missing(manifest) if why is None else []
        out.append(row)
    return out


# --- names and sidecars --------------------------------------------------------------------


def analyte_slug(manifest: dict[str, Any]) -> str:
    text = manifest["analyte"].get("label") or manifest["analyte"]["value"]
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40].strip("-")
    return slug or "analyte"


def published_path(manifest: dict[str, Any], entry: Entry) -> str:
    folder = "figures" if entry.kind == "figure" else "tables"
    name = f"{analyte_slug(manifest)}_{manifest['run_id']}_{entry.artifact}.{entry.extension}"
    return f"{sidecars.OUTPUTS}/{folder}/{name}"


def sidecar_path(rel: str) -> str:
    folder, _, name = rel.rpartition("/")
    return f"{folder}/{sidecars.sidecar_name(name)}"


def _local(iso: str) -> str:
    """reportifyr's ``%Y-%m-%d %H:%M:%S`` in local time, from our UTC ISO stamps."""
    stamp = datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    return stamp.astimezone().strftime("%Y-%m-%d %H:%M:%S")


def author() -> str:
    try:
        return getpass.getuser()
    except (OSError, KeyError):
        return "opncafyr"


def build_sidecar(
    manifest: dict[str, Any],
    entry: Entry,
    rel: str,
    blake3_hash: str,
    footnotes: sidecars.Footnotes,
    who: str,
) -> dict[str, Any]:
    """The reportifyr 0.4.0 sidecar of one published copy (``reportifyr-sidecar-v1``).

    Every artifact gets ``meta_type: "NA"``: the bundled footnote types carry canned text
    that states units (adapters/quartifyr/template/README.md), and units are never assumed.
    """
    run_id = manifest["run_id"]
    script = manifest.get("script", {}).get("path", "analysis.R")
    saved = manifest.get("export", {}).get("saved_at") or manifest["created_at"]
    sealed = manifest.get("sealed", {}).get("at") or saved
    analyte = manifest["analyte"]
    name = analyte.get("label") or analyte["value"]
    base = entry.artifact.removesuffix(FORMATTED)  # a formatted table uses its CSV's
    wanted = ABBREVIATIONS.get(base, DEFAULT_ABBREVIATIONS)
    tools = {f"{k}.version": str(v) for k, v in sorted(manifest.get("tools", {}).items())}
    return {
        "system_meta": {
            "platform": sys.platform,
            "software": {"version": f"opncafyr {__version__}", "packages_used": tools},
        },
        "source_meta": {
            "type": "script",
            "path": f"{runs_core.RUNS_DIR}/{run_id}/{script}",
            "creation_author": who,
            "latest_author": who,
            "creation_time": _local(saved),
            "latest_time": _local(sealed),
        },
        "object_meta": {
            "author": who,
            "path": rel,
            "creation_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "file_type": entry.extension,
            "meta_type": sidecars.NO_META_TYPE,
            "hash": blake3_hash,
            "table1": False,
            "footnotes": {
                "equations": [],
                "notes": [f"{entry.title}. Analyte: {name}. Run {run_id}."] + list(entry.notes),
                "abbreviations": [a for a in wanted if a in footnotes.abbreviations],
            },
        },
        "addl_meta": [],
    }


def _sidecar_bytes(doc: dict[str, Any]) -> bytes:
    return (json.dumps(doc, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


# --- plan and publish ----------------------------------------------------------------------


def _picks(raw: list[Pick]) -> list[Pick]:
    if not raw:
        raise PublishError("nothing_selected", "Choose at least one table or figure.")
    if len(raw) > MAX_ITEMS:
        raise PublishError("too_many", f"At most {MAX_ITEMS} items can be published at once.")
    if len(set(raw)) != len(raw):
        raise PublishError("duplicate", "The same artifact is selected twice.")
    return raw


def plan(root: Path, picks: list[Pick], footnotes: sidecars.Footnotes) -> Plan:
    """What publishing ``picks`` would write, checked in a scratch copy; writes nothing."""
    picks = _picks(picks)
    runs: dict[str, tuple[Path, dict[str, Any]]] = {}
    items: list[Planned] = []
    who = author()
    for pick in picks:
        if pick.run_id not in runs:
            runs[pick.run_id] = load_run(root, pick.run_id)
        folder, manifest = runs[pick.run_id]
        entry = next((e for e in catalog(manifest) if e.artifact == pick.artifact), None)
        if entry is None:
            raise PublishError(
                "unknown_artifact",
                f"Run {pick.run_id} has no publishable '{pick.artifact}'.",
                run_id=pick.run_id,
            )
        rel = published_path(manifest, entry)
        side_rel = sidecar_path(rel)
        target = resolve_in_root(root, rel)
        status = "new"
        side_bytes = b""
        reuse = False
        if target.exists() or target.is_symlink():
            same = (
                target.is_file()
                and not target.is_symlink()
                and runs_core.sha256_file(target) == entry.sha256
            )
            status = "unchanged" if same else "conflict"
            existing = resolve_in_root(root, side_rel)
            if same and not sidecars.check_artifact(root, rel, footnotes):
                side_bytes = existing.read_bytes()
                reuse = True
        if not side_bytes:
            blake = sidecars.blake3_file(resolve_in_root(folder, entry.path))
            doc = build_sidecar(manifest, entry, rel, blake, footnotes, who)
            side_bytes = _sidecar_bytes(doc)
        items.append(Planned(pick.run_id, entry, rel, side_rel, status, side_bytes, reuse))
    out = Plan(items, runs)
    # Check every sidecar as it would be written, in a scratch copy of OUTPUTS/.
    with tempfile.TemporaryDirectory(prefix="opncafyr-publish-") as tmp:
        scratch = Path(tmp)
        for item in items:
            if item.status == "conflict":
                continue
            folder = runs[item.run_id][0]
            dest = scratch / item.published_path
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(resolve_in_root(folder, item.entry.path), dest)
            (scratch / item.sidecar_path).write_bytes(item.sidecar_bytes)
        listed = [i.published_path for i in items if i.status != "conflict"]
        out.findings = sidecars.check_outputs(scratch, footnotes, listed)[1]
    return out


def copy_file(source: Path, target: Path) -> None:
    """Copy through a hidden temp file in the target folder, then rename into place."""
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as out, source.open("rb") as src:
            shutil.copyfileobj(src, out)
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _write_bytes(target: Path, data: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def new_publication_id(root: Path) -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    while True:
        pub_id = f"pub-{stamp}-{secrets.token_hex(2)}"
        if not resolve_in_root(root, f"{PUBLICATIONS_DIR}/{pub_id}.json").exists():
            return pub_id


def publish(
    root: Path, picks: list[Pick], footnotes: sidecars.Footnotes, label: str | None = None
) -> dict[str, Any]:
    """Copy ``picks`` into ``OUTPUTS/`` and record the publication. Returns its manifest."""
    with _lock:
        planned = plan(root, picks, footnotes)
        if planned.conflicts:
            raise PublishError(
                "name_conflict",
                "A published file with the same name but different content already exists. "
                "Published files are never replaced.",
                paths=[p.published_path for p in planned.conflicts],
            )
        if sidecars.blocking(planned.findings):
            raise PublishError(
                "sidecars_invalid",
                "The metadata sidecars did not pass their checks.",
                report=sidecars.preflight(planned.findings),
            )
        for item in planned.items:
            folder = planned.runs[item.run_id][0]
            if item.status == "new":
                source = resolve_in_root(folder, item.entry.path)
                copy_file(source, resolve_in_root(root, item.published_path))
            if not item.reuse_sidecar:
                _write_bytes(resolve_in_root(root, item.sidecar_path), item.sidecar_bytes)
        # Check what is now on disk before recording it.
        listed = [i.published_path for i in planned.items]
        findings = sidecars.check_outputs(root, footnotes, listed)[1]
        copies_ok = all(
            runs_core.sha256_file(resolve_in_root(root, i.published_path)) == i.entry.sha256
            for i in planned.items
        )
        if findings or not copies_ok:
            raise PublishError(
                "publish_failed",
                "The published copies don't match their runs; nothing was recorded.",
                report=sidecars.preflight(findings),
            )
        doc = _manifest(root, planned, label)
        atomic_write_json(
            resolve_in_root(root, f"{PUBLICATIONS_DIR}/{doc['publication_id']}.json"), doc, SCHEMA
        )
        return doc


def _manifest(root: Path, planned: Plan, label: str | None) -> dict[str, Any]:
    runs = []
    for run_id, (folder, manifest) in planned.runs.items():
        analyte = manifest["analyte"]
        runs.append(
            {
                "run_id": run_id,
                "analyte": {
                    "column": analyte["column"],
                    "value": analyte["value"],
                    "label": analyte.get("label"),
                },
                "manifest_sha256": runs_core.sha256_file(folder / runs_core.MANIFEST),
                "replay_sha256": manifest["replay"]["sha256"],
            }
        )
    doc: dict[str, Any] = {
        "schema_version": "1.0",
        "publication_id": new_publication_id(root),
        "created_at": runs_core.now(),
        "tools": {"opncafyr": __version__, "sidecar_format": SIDECAR_FORMAT},
        "runs": runs,
        "items": [
            {
                "run_id": i.run_id,
                "artifact": i.entry.artifact,
                "title": i.entry.title,
                "kind": i.entry.kind,
                "artifact_path": i.entry.path,
                "artifact_sha256": i.entry.sha256,
                "published_path": i.published_path,
                "published_sha256": runs_core.sha256_file(resolve_in_root(root, i.published_path)),
                "sidecar_path": i.sidecar_path,
                "sidecar_sha256": runs_core.sha256_file(resolve_in_root(root, i.sidecar_path)),
                "contains_subject_ids": i.entry.contains_subject_ids,
            }
            for i in planned.items
        ],
    }
    if label:
        doc["label"] = label
    return doc


# --- reading publications ------------------------------------------------------------------


def read_publication(root: Path, publication_id: str) -> dict[str, Any]:
    from jsonschema import ValidationError

    if not PUBLICATION_ID_RE.match(publication_id):
        raise PublishError("not_found", "There is no such publication.")
    path = resolve_in_root(root, f"{PUBLICATIONS_DIR}/{publication_id}.json")
    if path.is_symlink() or not path.is_file():
        raise PublishError("not_found", "There is no such publication.")
    try:
        doc = json.loads(path.read_text("utf-8"))
        validate(doc, SCHEMA)
    except (OSError, ValueError, ValidationError):
        raise PublishError("invalid", f"Publication {publication_id} is not valid.") from None
    if doc["publication_id"] != publication_id:
        raise PublishError("invalid", f"Publication {publication_id} names another id.")
    return doc


def verify_publication(root: Path, doc: dict[str, Any]) -> list[str]:
    """Published files or sidecars that no longer match the manifest (paths)."""
    bad = []
    for item in doc["items"]:
        for key in ("published", "sidecar"):
            rel = item[f"{key}_path"]
            path = resolve_in_root(root, rel)
            plain = path.is_file() and not (root / rel).is_symlink()
            if not plain or runs_core.sha256_file(path) != item[f"{key}_sha256"]:
                bad.append(rel)
    return bad


def recorded_paths(root: Path) -> set[str]:
    """Every OUTPUTS/ path that a valid publication lists."""
    from opncafyr.core.artifacts import list_publications

    out: set[str] = set()
    for listed in list_publications(root):
        if listed.get("state") != "recorded":
            continue
        try:
            doc = read_publication(root, listed["publication_id"])
        except PublishError:
            continue
        for item in doc["items"]:
            out.update((item["published_path"], item["sidecar_path"]))
    return out
