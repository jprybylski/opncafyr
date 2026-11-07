"""The redacted support bundle (#38): what support needs, and nothing it shouldn't have.

A bundle is a zip of a fixed list of text entries (``ENTRIES``), written only to
``<app root>/logs/support/support-<time>.zip`` on this machine. Nothing is uploaded. The
home page shows the entries before anything is saved (``GET /api/support/preview``), and
``opncafyr doctor --bundle`` prints them and asks first.

What goes in:
- versions: opncafyr, Python, OS and architecture, the active install and its components;
- whether the install's files still match their pinned hashes (``installer.payload``)
  and are all present (``installer.install.quick_check``), the install records and the
  latest installation report;
- tool health and web-app states;
- for an open study folder: its folder name, counts of what ``.opncafyr/`` holds, and the
  20 most recent jobs: kind, state, reason, exit code, times and their short message;
- the tail of the launcher log, each child app's log and the latest installer log.

What never goes in (ADR 0007): dataset content, snapshots, results, job ``stdout.log`` and
``stderr.log`` (only their sizes), file names inside the study, the recent-workspaces list
and the environment.

Free text goes through ``Scrubber.free``: secrets and tokens are masked, the study
folder's path becomes ``<study:NAME>``, the home folder ``~``, the user name ``<user>``,
registered identifier values ``‹redacted›`` (``core.redaction`` plus the values each
workflow pack's ``sensitive_values`` hook finds in the study), and every decimal number
``‹n›``, so a concentration quoted in an error message can't get through. Structured fields
(versions, ids, states, exit codes, times) are copied as they are; installer logs, which
never hold study data, get the path and secret rules only.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import platform
import re
import shutil
import sys
import time
import zipfile
from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from opncafyr import __version__
from opncafyr.core.manifest import atomic_write_bytes, validate
from opncafyr.core.redaction import REDACTOR, Redactor
from opncafyr.installer import payload as payload_mod
from opncafyr.installer.layout import INSTALL_RECORD, Layout
from opncafyr.installer.support import redact as redact_secrets

SUPPORT_DIR = "support"  # under <app root>/logs
BUNDLE_NAME_RE = re.compile(r"^(?:support|install-failure)-\d{8}-\d{6}(?:-\d{1,3})?\.zip$")
RECENT_JOBS = 20
LAUNCHER_LOG_LINES = 500
CHILD_LOG_LINES = 100
INSTALL_LOG_LINES = 300
NUMBER = "‹n›"
ENTRIES = (
    "README.txt",
    "system.json",
    "install.json",
    "runtime.json",
    "tools.json",
    "jobs.json",
    "logs/launcher.log",
    "logs/install/installer.log",
)  # plus logs/children/<tool>.log for each child app that has a log
_CHILD_LOG_RE = re.compile(r"^[a-z][a-z0-9_-]*\.log$")
# A decimal number: 124.137, 12,4, -0.020, 1.5e-3. Not part of a dotted version (0.2.9,
# 4.6.2) or of a word or id.
_DECIMAL = re.compile(r"(?<![\w.])[-+]?\d+[.,]\d+(?:[eE][-+]?\d+)?(?![\w]|[.,]\d)")
_JOB_FIELDS = (
    "job_id",
    "kind",
    "pool",
    "state",
    "reason",
    "exit_code",
    "created_at",
    "started_at",
    "finished_at",
    "updated_at",
    "timeout_s",
)

README = """opncafyr support bundle
=======================

Created by opncafyr {version} at {created}. It was saved on this computer only;
opncafyr never sends it anywhere. Share it with support yourself if you choose to.

Research use only. opncafyr has not been validated for clinical or regulatory decisions.

Included:
  system.json    opncafyr, Python and OS versions, architecture, free disk space
  install.json   the active install, its components and smoke tests, whether its files
                 still match their pinned hashes, and the latest installation report
  runtime.json   which runtime programs are present (file names only)
  tools.json     each tool's health and web-app state
  jobs.json      for the open study folder: counts, and the {jobs} most recent jobs
                 (kind, state, exit code, times and a short message)
  logs/          the end of the launcher, app and installer logs

Never included: data, snapshots, results, job output logs (only their sizes), file
names inside the study folder, or the list of recent folders.

Paths are shortened: the study folder is <study:NAME>, your home folder is ~ and your
user name is <user>. Identifier values are replaced by ‹redacted› and decimal numbers by
‹n›. Secrets and tokens are masked.
"""


@dataclass(frozen=True)
class Entry:
    name: str
    text: str


class Scrubber:
    """The redaction rules for one bundle."""

    def __init__(
        self,
        *,
        workspace: Path | None = None,
        values: Iterable[str] = (),
        home: str | None = None,
        user: str | None = None,
    ) -> None:
        self.home = home
        self.user = user
        self.study: list[tuple[str, str]] = []
        if workspace is not None:
            label = f"<study:{workspace.name}>"
            variants = {str(workspace)}
            with contextlib.suppress(OSError):
                variants.add(str(workspace.resolve()))
            for v in list(variants):
                variants |= {v.replace("\\", "/"), v.replace("/", "\\"), v.replace("\\", "\\\\")}
            self.study = [(v, label) for v in sorted(variants, key=len, reverse=True) if len(v) > 1]
        self.values = Redactor()
        self.values.register_values(values)

    def path(self, text: str) -> str:
        """Secrets, the study folder, the home folder and the user name."""
        for variant, label in self.study:
            text = text.replace(variant, label)
        return redact_secrets(text, home=self.home, user=self.user)

    def free(self, text: str) -> str:
        """``path``, then identifier values and decimal numbers: for any free text."""
        text = self.path(text)
        text = self.values.redact(REDACTOR.redact(text))
        return _DECIMAL.sub(NUMBER, text)


def _tree(value: Any, fn: Callable[[str], str]) -> Any:
    if isinstance(value, str):
        return fn(value)
    if isinstance(value, Mapping):
        return {str(k): _tree(v, fn) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_tree(v, fn) for v in value]
    return value


def _json(doc: Any) -> str:
    return json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def _tail(path: Path, lines: int) -> list[str]:
    try:
        with path.open("rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - lines * 400))
            data = fh.read()
    except OSError:
        return []
    out = data.decode("utf-8", "replace").splitlines()
    if size > lines * 400 and out:
        out = out[1:]  # the first line was cut
    return out[-lines:]


def _size(path: Path) -> int | None:
    try:
        return path.stat().st_size
    except OSError:
        return None


# --- entries ---------------------------------------------------------------------------


def _system(root: Path, install_id: str | None, now: float) -> dict[str, Any]:
    try:
        free_mb = shutil.disk_usage(root if root.exists() else Path.home()).free // (1 << 20)
    except OSError:
        free_mb = None
    return {
        "opncafyr": __version__,
        "python": sys.version.split()[0],
        "os": platform.platform(),
        "machine": platform.machine(),
        "free_disk_mb": free_mb,
        "install_id": install_id,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(now)),
    }


def _install(layout: Layout, install_id: str | None, scrub: Scrubber) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "active": install_id,
        "complete": layout.complete_ids() if layout.versions.is_dir() else [],
        "incomplete": layout.incomplete_ids() if layout.versions.is_dir() else [],
    }
    if install_id is not None:
        record = layout.record(install_id)
        if record is None:
            doc["record"] = f"{INSTALL_RECORD} is missing or incomplete"
        else:
            doc["record"] = {
                k: record.get(k)
                for k in (
                    "install_id",
                    "version",
                    "platform",
                    "os",
                    "mode",
                    "options",
                    "installed_at",
                    "payload_digest",
                    "bundle_id",
                )
                if k in record
            }
            doc["record"]["source"] = scrub.path(str(record.get("source", "")))
            doc["record"]["components"] = [
                {"name": c.get("name"), "version": c.get("version")}
                for c in record.get("components", [])
            ]
            doc["record"]["smoke"] = _tree(record.get("smoke", []), scrub.path)
        doc["payload"] = _payload(layout.version_dir(install_id) / "installer", record)
        from opncafyr.installer.install import quick_check

        doc["problems"] = [scrub.path(p) for p in quick_check(layout, install_id)]
    reports = sorted((layout.state / "reports").glob("install-report-*.json"))
    if reports:
        try:
            report = json.loads(reports[-1].read_text("utf-8"))
        except (OSError, ValueError) as exc:
            report = f"unreadable: {type(exc).__name__}"
        doc["latest_report"] = {"file": reports[-1].name, "report": _tree(report, scrub.path)}
    support = layout.logs / SUPPORT_DIR
    doc["install_failure_bundles"] = sorted(p.name for p in support.glob("install-failure-*.zip"))
    return doc


def _payload(directory: Path, record: Mapping[str, Any] | None) -> dict[str, Any]:
    """Whether the installer payload copy still matches its own hashes and the record."""
    try:
        loaded = payload_mod.load(directory, verify=True)
    except Exception as exc:  # noqa: BLE001 - any failure is the finding
        return {"ok": False, "error": type(exc).__name__, "detail": _first_line(str(exc))}
    digest_ok = record is None or record.get("payload_digest") in (None, loaded.digest)
    return {
        "ok": digest_ok,
        "files": len(loaded.files),
        "digest_matches_record": digest_ok,
    }


def _first_line(text: str) -> str:
    return text.strip().splitlines()[0][:300] if text.strip() else ""


def _runtime(runtime: Any) -> dict[str, Any]:
    if runtime is None:
        return {"source": None, "executables": {}}
    exes = {
        name: {"file": Path(path).name, "exists": Path(path).is_file()}
        for name, path in sorted(runtime.executables.items())
    }
    lib = runtime.r_library
    return {
        "source": Path(runtime.source).name if runtime.source else None,
        "executables": exes,
        "r_library": None if lib is None else {"exists": Path(lib).is_dir()},
    }


def _tools(tools: Mapping[str, Any], health: Any, apps: Mapping[str, str]) -> dict[str, Any]:
    out = []
    for tool in tools.values():
        status = health.status(tool.id).as_json() if health is not None else {}
        out.append(
            {
                "id": tool.id,
                "version": tool.version,
                "capabilities": list(tool.capabilities),
                **status,
                "web_app": apps.get(tool.id),
            }
        )
    return {"tools": out}


def _jobs(workspace: Path, scrub: Scrubber) -> dict[str, Any]:
    meta = workspace / ".opncafyr"
    counts = (
        {
            p.name: sum(1 for c in p.iterdir() if not c.name.startswith("."))
            for p in sorted(meta.iterdir())
            if p.is_dir() and not p.name.startswith(".")
        }
        if meta.is_dir()
        else {}
    )
    records = []
    jobs_dir = meta / "jobs"
    for folder in sorted(jobs_dir.iterdir()) if jobs_dir.is_dir() else []:
        try:
            state = json.loads((folder / "state.json").read_text("utf-8"))
            validate(state, "job-v1")
        except Exception:  # noqa: BLE001, S112 - an unreadable record is skipped
            continue
        records.append((folder, state))
    records.sort(key=lambda r: r[1].get("created_at") or "", reverse=True)
    recent = []
    for folder, state in records[:RECENT_JOBS]:
        job = {k: state.get(k) for k in _JOB_FIELDS if k in state}
        job["message"] = scrub.free(str(state.get("message") or ""))
        job["logs"] = {name: _size(folder / name) for name in ("stdout.log", "stderr.log")}
        try:
            diag = json.loads((folder / "diagnostic.json").read_text("utf-8"))
        except (OSError, ValueError):
            diag = None
        if isinstance(diag, dict):
            job["diagnostic"] = {
                k: diag[k]
                for k in ("state_before", "staging_kept", "state_at_stop", "orphan_process_killed")
                if k in diag
            }
        recent.append(job)
    return {
        "workspace": f"<study:{workspace.name}>",
        "counts": counts,
        "jobs_by_state": dict(Counter(s.get("state") for _, s in records)),
        "recent_jobs": recent,
    }


def _launcher_log(logs: Path, scrub: Scrubber) -> str:
    out = []
    for line in _tail(logs / "launcher.log", LAUNCHER_LOG_LINES):
        try:
            rec = json.loads(line)
        except ValueError:
            out.append(scrub.free(line))
            continue
        if not isinstance(rec, dict):
            out.append(scrub.free(line))
            continue
        kept = {k: rec.get(k) for k in ("ts", "level", "logger") if k in rec}
        for key in ("message", "exception"):
            if key in rec:
                kept[key] = scrub.free(str(rec[key]))
        out.append(json.dumps(kept, ensure_ascii=False))
    return "\n".join(out) + ("\n" if out else "")


def _install_log(logs: Path, scrub: Scrubber) -> tuple[str, dict[str, Any]]:
    runs = sorted(p for p in (logs / "install").glob("*") if p.is_dir())
    if not runs:
        return "", {}
    latest = runs[-1]
    lines = [scrub.path(line) for line in _tail(latest / "installer.log", INSTALL_LOG_LINES)]
    files = [{"name": p.name, "bytes": _size(p)} for p in sorted(latest.glob("*.log"))]
    return "\n".join(lines) + ("\n" if lines else ""), {"folder": latest.name, "files": files}


def build(
    *,
    app_root: Path,
    tools: Mapping[str, Any],
    health: Any = None,
    runtime: Any = None,
    apps: Mapping[str, str] | None = None,
    workspace: Path | None = None,
    sensitive: Iterable[str] = (),
    now: float | None = None,
) -> list[Entry]:
    """The bundle's entries, in order. Reads files; writes nothing."""
    now = time.time() if now is None else now
    scrub = Scrubber(workspace=workspace, values=sensitive)
    layout = Layout(app_root)
    install_id = layout.read_current() if layout.current.exists() else None
    install = _install(layout, install_id, scrub)
    install_text, install_files = _install_log(layout.logs, scrub)
    install["latest_install_log"] = install_files
    entries = [
        Entry(
            "README.txt",
            README.format(
                version=__version__,
                created=time.strftime("%Y-%m-%d %H:%M", time.localtime(now)),
                jobs=RECENT_JOBS,
            ),
        ),
        Entry("system.json", _json(_system(app_root, install_id, now))),
        Entry("install.json", _json(install)),
        Entry("runtime.json", _json(_runtime(runtime))),
        Entry("tools.json", _json(_tools(tools, health, apps or {}))),
        Entry(
            "jobs.json",
            _json(_jobs(workspace, scrub) if workspace is not None else {"workspace": None}),
        ),
        Entry("logs/launcher.log", _launcher_log(layout.logs, scrub)),
        Entry("logs/install/installer.log", install_text),
    ]
    children = layout.logs / "children"
    for log in sorted(children.glob("*.log")) if children.is_dir() else []:
        if _CHILD_LOG_RE.match(log.name):
            lines = [scrub.free(line) for line in _tail(log, CHILD_LOG_LINES)]
            entries.append(Entry(f"logs/children/{log.name}", "\n".join(lines) + "\n"))
    return entries


def zip_bytes(entries: Iterable[Entry]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for entry in entries:
            zf.writestr(entry.name, entry.text)
    return buf.getvalue()


def write(entries: Iterable[Entry], app_root: Path, now: float | None = None) -> Path:
    """Save the bundle as ``<app root>/logs/support/support-<time>.zip``; return its path."""
    now = time.time() if now is None else now
    folder = Layout(app_root).logs / SUPPORT_DIR
    folder.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(now))
    path = folder / f"support-{stamp}.zip"
    n = 1
    while path.exists():
        n += 1
        path = folder / f"support-{stamp}-{n}.zip"
    return atomic_write_bytes(path, zip_bytes(entries))


def saved_bundles(app_root: Path) -> list[dict[str, Any]]:
    """The support and install-failure bundles in ``logs/support``, newest first."""
    folder = Layout(app_root).logs / SUPPORT_DIR
    if not folder.is_dir():
        return []
    found = [p for p in folder.iterdir() if p.is_file() and BUNDLE_NAME_RE.match(p.name)]
    found.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return [{"name": p.name, "bytes": p.stat().st_size} for p in found]


def bundle_path(app_root: Path, name: str) -> Path | None:
    """The saved bundle called ``name``, or ``None`` if the name isn't a bundle name."""
    if not BUNDLE_NAME_RE.match(name):
        return None
    path = Layout(app_root).logs / SUPPORT_DIR / name
    return path if path.is_file() else None


def sensitive_values(packs: Iterable[Any], workspace: Path | None) -> list[str]:
    """Identifier values each workflow pack finds in the study. A failing hook adds none."""
    if workspace is None:
        return []
    values: list[str] = []
    for pack in packs:
        hook = getattr(pack, "sensitive_values", None)
        if hook is None:
            continue
        try:
            values.extend(str(v) for v in hook(workspace))
        except Exception:  # noqa: BLE001, S112 - redaction falls back to the other rules
            continue
    return values
