"""Report and deck projects bound to one publication (#32, #33).

``report/`` and ``deck/`` are self-contained tool projects in the study folder. Each is
bound to one publication by ``.opncafyr/<tool>.json`` (``publish-binding-v1``).

- **OUTPUTS copy.** The launcher keeps a copy of the publication's files in
  ``<project>/OUTPUTS/``, so the tool and its editor find them inside the project root.
  deckifyr refuses an ``outputs_dir`` outside its root. That folder is managed: files the
  publication doesn't list are removed from it.
- **Builds.** A build never trusts that copy. It assembles a staging copy of the project
  and copies the publication's files in from the study's ``OUTPUTS/``, each checked
  against the publication's SHA-256 first. So a report or deck sees only the selected
  publication's files, at the bytes that were published.
- **Inputs.** Each build's inputs are hashed by the launcher before the tool runs.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jsonschema import ValidationError

from opncafyr.core import runs as runs_core
from opncafyr.core.manifest import atomic_write_json, validate
from opncafyr.core.workspace import resolve_in_root
from opncafyr.paths import WORKSPACE_META_DIR
from opncafyr.workflows.nca.publish import curator
from opncafyr.workflows.nca.publish.curator import PublishError

BINDING_SCHEMA = "publish-binding-v1"
BUILD_SCHEMA = "publish-build-v1"
OUTPUTS = "OUTPUTS"
OUTPUT_FOLDERS = ("figures", "tables", "listings")
BUILDS = "builds"


def now() -> str:
    return runs_core.now()


def new_build_id() -> str:
    import secrets

    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + secrets.token_hex(2)


# --- publications --------------------------------------------------------------------------


def publication_sha256(root: Path, publication_id: str) -> str:
    path = resolve_in_root(root, f"{curator.PUBLICATIONS_DIR}/{publication_id}.json")
    return runs_core.sha256_file(path)


def load_publication(root: Path, publication_id: str) -> tuple[dict[str, Any], str]:
    """A recorded publication whose files in ``OUTPUTS/`` still match, and its hash."""
    doc = curator.read_publication(root, publication_id)
    changed = curator.verify_publication(root, doc)
    if changed:
        raise PublishError(
            "publication_changed",
            f"Files of publication {publication_id} changed after it was recorded: "
            f"{', '.join(changed[:5])}. Publish again to make a new publication.",
            paths=changed,
        )
    return doc, publication_sha256(root, publication_id)


def copy_publication(root: Path, publication: dict[str, Any], dest: Path) -> list[str]:
    """Copy the publication's files (and sidecars) into ``dest/OUTPUTS``; check each one.

    Returns the published paths. Raises ``PublishError`` if a file no longer matches.
    """
    copied = []
    for item in publication["items"]:
        for key in ("published", "sidecar"):
            rel = item[f"{key}_path"]
            source = resolve_in_root(root, rel)
            if (root / rel).is_symlink() or not source.is_file():
                raise PublishError("publication_changed", f"{rel} is missing.")
            target = dest / rel
            curator.copy_file(source, target)
            if runs_core.sha256_file(target) != item[f"{key}_sha256"]:
                raise PublishError(
                    "publication_changed", f"{rel} doesn't match its publication any more."
                )
        copied.append(item["published_path"])
    return copied


def sync_outputs(root: Path, project: Path, publication: dict[str, Any]) -> None:
    """Make ``<project>/OUTPUTS`` hold exactly the publication's files."""
    wanted = {p for i in publication["items"] for p in (i["published_path"], i["sidecar_path"])}
    base = project / OUTPUTS
    for folder in OUTPUT_FOLDERS:
        (base / folder).mkdir(parents=True, exist_ok=True)
    for path in sorted(base.rglob("*")):
        rel = path.relative_to(project).as_posix()
        if path.is_symlink() or (path.is_file() and rel not in wanted):
            path.unlink()
    copy_publication(root, publication, project)


def outputs_match(project: Path, publication: dict[str, Any]) -> list[str]:
    """Paths in ``<project>/OUTPUTS`` that are missing or differ from the publication."""
    bad = []
    for item in publication["items"]:
        for key in ("published", "sidecar"):
            path = project / item[f"{key}_path"]
            plain = path.is_file() and not path.is_symlink()
            if not plain or runs_core.sha256_file(path) != item[f"{key}_sha256"]:
                bad.append(item[f"{key}_path"])
    return bad


# --- bindings ------------------------------------------------------------------------------


def binding_path(root: Path, tool: str) -> Path:
    return resolve_in_root(root, f"{WORKSPACE_META_DIR}/{tool}.json")


def read_binding(root: Path, tool: str) -> dict[str, Any] | None:
    path = binding_path(root, tool)
    if path.is_symlink() or not path.is_file():
        return None
    try:
        doc = json.loads(path.read_text("utf-8"))
        validate(doc, BINDING_SCHEMA)
    except (OSError, ValueError, ValidationError):
        return None
    return doc if doc["tool"] == tool else None


def binding(
    tool: str,
    project: str,
    publication_id: str,
    publication_sha: str,
    generated: dict[str, str],
    settings: dict[str, Any] | None = None,
    standards: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """A binding. ``standards`` is the imported template the project was set up from (#70)."""
    doc = {
        "schema_version": 1,
        "tool": tool,
        "project": project,
        "publication_id": publication_id,
        "publication_sha256": publication_sha,
        "generated": generated,
        "settings": settings or {},
        "updated_at": now(),
    }
    if standards is not None:
        doc["standards"] = standards
    return doc


def write_binding(root: Path, doc: dict[str, Any]) -> None:
    atomic_write_json(binding_path(root, doc["tool"]), doc, BINDING_SCHEMA)


def edited(project: Path, generated: dict[str, str]) -> list[str]:
    """Generated source files the user has changed (or removed) since."""
    out = []
    for rel, sha in sorted(generated.items()):
        path = project / rel
        if not path.is_file() or runs_core.sha256_file(path) != sha:
            out.append(rel)
    return out


# --- staging copies and hashes -------------------------------------------------------------


def copy_project(project: Path, dest: Path, skip: Callable[[str], bool]) -> None:
    """Copy a project's files (no links) into ``dest``, leaving out what ``skip`` names."""
    for path in sorted(project.rglob("*")):
        rel = path.relative_to(project).as_posix()
        if skip(rel):
            continue
        if path.is_symlink():
            raise PublishError("unsafe_project", f"{project.name}/{rel} is a link.")
        target = dest / rel
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        elif path.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)


def under(rel: str, *prefixes: str) -> bool:
    return any(rel == p or rel.startswith(p + "/") for p in prefixes)


def tree_sha256(folder: Path) -> str:
    """One hash for a folder: SHA-256 over sorted ``<relpath>  <file sha256>`` lines."""
    lines = sorted(
        f"{p.relative_to(folder).as_posix()}  {runs_core.sha256_file(p)}"
        for p in folder.rglob("*")
        if p.is_file()
    )
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def hash_inputs(project: Path, roles: Iterable[tuple[str, str]]) -> list[dict[str, str]]:
    """``[{role, path, sha256}]`` for each existing ``(role, relpath)``; folders as trees."""
    out = []
    for role, rel in roles:
        path = project / rel
        if path.is_dir():
            out.append({"role": role, "path": rel, "sha256": tree_sha256(path)})
        elif path.is_file():
            out.append({"role": role, "path": rel, "sha256": runs_core.sha256_file(path)})
    return out


def output_roles(publication: dict[str, Any]) -> list[tuple[str, str]]:
    roles = []
    for item in publication["items"]:
        roles.append((item["kind"], item["published_path"]))
        roles.append(("sidecar", item["sidecar_path"]))
    return roles


def build_manifest(
    *,
    build_id: str,
    tool: str,
    publication: dict[str, Any],
    publication_sha: str,
    inputs: list[dict[str, str]],
    tools: dict[str, str | None],
    settings: dict[str, Any],
    outputs: list[dict[str, Any]],
    warnings: list[dict[str, Any]],
    tool_result: dict[str, str],
    standards: dict[str, Any] | None = None,
) -> dict[str, Any]:
    doc = {
        "schema_version": 1,
        "build_id": build_id,
        "tool": tool,
        "created_at": now(),
        "publication_id": publication["publication_id"],
        "publication_sha256": publication_sha,
        "runs": [r["run_id"] for r in publication["runs"]],
        "items": [
            {
                "run_id": i["run_id"],
                "published_path": i["published_path"],
                "sha256": i["published_sha256"],
                "sidecar_sha256": i["sidecar_sha256"],
            }
            for i in publication["items"]
        ],
        "inputs": inputs,
        "tools": tools,
        "settings": settings,
        "outputs": outputs,
        "warnings": warnings,
        "tool_result": tool_result,
    }
    if standards is not None:
        doc["standards"] = standards
    validate(doc, BUILD_SCHEMA)
    return doc


def list_builds(root: Path, project: str) -> list[dict[str, Any]]:
    """Every valid build of ``project``, newest first (a folder without one is skipped)."""
    folder = resolve_in_root(root, f"{project}/{BUILDS}")
    if not folder.is_dir() or folder.is_symlink():
        return []
    out = []
    for entry in sorted(folder.iterdir(), key=lambda p: p.name, reverse=True):
        doc = read_build(root, project, entry.name)
        if doc is not None:
            out.append(doc)
    return out


def read_build(root: Path, project: str, build_id: str) -> dict[str, Any] | None:
    import re

    if not re.fullmatch(r"[0-9]{8}T[0-9]{6}Z-[0-9a-f]{4}", build_id):
        return None
    path = resolve_in_root(root, f"{project}/{BUILDS}/{build_id}/build.json")
    if path.is_symlink() or not path.is_file():
        return None
    try:
        doc = json.loads(path.read_text("utf-8"))
        validate(doc, BUILD_SCHEMA)
    except (OSError, ValueError, ValidationError):
        return None
    return doc if doc["build_id"] == build_id else None


def build_output(root: Path, project: str, build_id: str, role: str) -> Path | None:
    """The output file of a build, if it still matches its manifest."""
    doc = read_build(root, project, build_id)
    if doc is None:
        return None
    entry = next((o for o in doc["outputs"] if o["role"] == role), None)
    if entry is None:
        return None
    path = resolve_in_root(root, f"{project}/{BUILDS}/{build_id}/{entry['path']}")
    if path.is_symlink() or not path.is_file() or runs_core.sha256_file(path) != entry["sha256"]:
        return None
    return path


# --- a project's jobs ----------------------------------------------------------------------


class ProjectJobs:
    """What the report and deck services share: their jobs and their bound publication."""

    tool = ""
    project_name = ""

    def __init__(self) -> None:
        import threading

        self.lock = threading.Lock()
        self.jobs: dict[str, str] = {}  # step -> latest job id

    def project(self, root: Path) -> Path:
        return resolve_in_root(root, self.project_name)

    def busy(self, manager: Any) -> bool:
        from opncafyr.core.jobs import TERMINAL_STATES, JobState

        for job_id in self.jobs.values():
            try:
                if JobState(manager.get(job_id)["state"]) not in TERMINAL_STATES:
                    return True
            except KeyError:
                continue
        return False

    def refuse_if_busy(self, manager: Any) -> None:
        if self.busy(manager):
            raise PublishError("busy", f"A {self.tool} job is still running.")

    def bound(self, root: Path) -> tuple[dict[str, Any], str]:
        """The bound publication (files still intact) and its hash."""
        doc = read_binding(root, self.tool)
        if doc is None or not self.project(root).is_dir():
            raise PublishError("no_project", f"Set up the {self.tool} for a publication first.")
        publication, sha = load_publication(root, doc["publication_id"])
        if sha != doc["publication_sha256"]:
            raise PublishError("publication_changed", "The bound publication's record changed.")
        return publication, sha

    def job_states(self, manager: Any) -> dict[str, dict[str, Any]]:
        out = {}
        for step, job_id in self.jobs.items():
            try:
                job = manager.get(job_id) if manager is not None else None
            except KeyError:
                job = None
            if job is not None:
                out[step] = {k: job.get(k) for k in ("job_id", "state", "message", "output")}
        return out

    def base_status(self, manager: Any, root: Path) -> dict[str, Any]:
        project = self.project(root)
        doc = read_binding(root, self.tool)
        jobs = self.job_states(manager)
        out: dict[str, Any] = {
            "project": project.is_dir(),
            "binding": doc,
            "edited": edited(project, doc["generated"]) if doc and project.is_dir() else [],
            "jobs": jobs,
            "check": None,
            "builds": list_builds(root, self.project_name),
        }
        check = jobs.get("check")
        if check and check["state"] == "completed" and check.get("output"):
            path = resolve_in_root(root, f"{check['output']}/preflight.json")
            if path.is_file():
                out["check"] = json.loads(path.read_text("utf-8"))
        return out
