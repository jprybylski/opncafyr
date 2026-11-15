"""Optional datum for external data (#83): the pinned release binary (``[optional.datum]``).

Installed by default, but best effort, like Git (#56). Any failure here (a damaged archive,
a missing file, a binary that won't start, reports another version or was built without
its Git handler) is reported in
``ctx.optional``, which goes into the install record and the installation report, and the
install goes on without datum: the External data page is unavailable and nothing else
depends on it. The launcher uses only this binary, never one found on ``PATH``.

The release archive holds ``datum`` (``datum.exe`` on Windows), ``LICENSE`` and
``README.md`` at its root; it unpacks into ``versions/<id>/datum``. The check is
``datum --json --version`` (datum 1.7.0, #85): ``{"format", "version", "features"}``, where
``version`` must be the pinned one and ``features`` must list ``git``.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from opncafyr.installer import proc
from opncafyr.installer.archives import extract
from opncafyr.installer.pins import DATUM
from opncafyr.installer.steps import WINDOWS, Context

EXECUTABLE = "datum"  # the runtime.json name


def binary_for(platform: str) -> str:
    return "datum.exe" if platform.startswith("windows") else "datum"


def install(ctx: Context) -> None:
    if not ctx.with_datum:
        ctx.report_optional(DATUM, "skipped", "not requested (--without-datum)")
        return
    if DATUM in ctx.optional:
        return  # its file couldn't be fetched; the installer reported it already
    if not any(a.component == DATUM for a in ctx.assets):
        ctx.report_optional(DATUM, "skipped", "not in the pins")
        return
    dest = ctx.dir / "datum"
    try:
        detail = _install(ctx, dest)
    except Exception as exc:  # noqa: BLE001 - optional: report it and go on without datum
        shutil.rmtree(dest, ignore_errors=True)
        ctx.executables.pop(EXECUTABLE, None)
        ctx.components.pop(DATUM, None)
        detail = f"{type(exc).__name__}: {exc}"[-500:]
        ctx.report_optional(DATUM, "failed", detail)
        _log(ctx, f"failed: {detail}")
        ctx.say(
            "warning: datum (optional) was not installed, so the External data page is "
            f"unavailable; everything else is installed. {detail}"
        )
        return
    ctx.report_optional(DATUM, "installed", detail)
    _log(ctx, detail)


def _install(ctx: Context, dest: Path) -> str:
    asset = ctx.asset(DATUM)
    shutil.rmtree(dest, ignore_errors=True)
    extract(ctx.file(DATUM), dest)
    name = binary_for(ctx.platform)
    exe = dest / name
    if not exe.is_file() or exe.is_symlink():
        raise FileNotFoundError(f"the datum archive has no {name}")
    if not WINDOWS and not os.access(exe, os.X_OK):
        raise PermissionError(f"{name} is not executable")
    done = proc.capture([exe, "--json", "--version"], env=ctx.env())
    out = done.stdout.decode(errors="replace").strip()
    try:
        doc = json.loads(out)
    except ValueError:
        doc = None
    version = doc.get("version") if isinstance(doc, dict) else None
    if done.returncode != 0 or version != asset.version:
        err = done.stderr.decode(errors="replace").strip()
        raise RuntimeError(f"datum reports {(out or err)[-200:]!r}, pinned {asset.version}")
    features = doc.get("features") if isinstance(doc, dict) else None
    if not isinstance(features, list) or "git" not in features:
        raise RuntimeError(f"datum {version} was built without its Git handler")
    ctx.executables[EXECUTABLE] = exe
    ctx.record(DATUM, asset.version, [asset])
    return f"datum v{version}"


def _log(ctx: Context, line: str) -> None:
    try:
        with ctx.log("datum").open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass
