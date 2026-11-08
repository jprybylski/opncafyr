"""Optional Git for version history (#56): the pinned portable build (``[optional.git]``).

Installed by default, but best effort. Any failure here (a damaged archive, a missing
file, a Git that won't start) is reported in ``ctx.optional``, which goes into the install
record and the installation report, and the install goes on without Git: version history
is unavailable and nothing else depends on it. The installer uses only this Git, never one
found on ``PATH``.

The archive (dugite-native) unpacks into ``versions/<id>/git``: ``bin/git`` and
``libexec/git-core`` on macOS, ``cmd/git.exe`` and ``ucrt64/libexec/git-core`` on Windows.
The macOS build isn't relocatable on its own (its compiled exec path is ``/``), so every
caller passes ``--exec-path=<exec_path(git)>``. ``runtime.json`` names only the ``git``
executable; the exec path follows from it.
"""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

from opncafyr.installer import proc
from opncafyr.installer.archives import extract
from opncafyr.installer.pins import GIT
from opncafyr.installer.steps import WINDOWS, Context

EXECUTABLE = "git"  # the runtime.json name


def layout_for(platform: str) -> tuple[str, str]:
    """(executable, exec path) inside the unpacked archive, for ``platform``."""
    if platform.startswith("windows"):
        return "cmd/git.exe", "ucrt64/libexec/git-core"
    return "bin/git", "libexec/git-core"


def exec_path(git: Path) -> Path:
    """The ``--exec-path`` for an installed Git executable (``<root>/{bin,cmd}/git``)."""
    root = Path(git).parent.parent
    if Path(git).suffix.lower() == ".exe":
        return root / "ucrt64" / "libexec" / "git-core"
    return root / "libexec" / "git-core"


def install(ctx: Context) -> None:
    if not ctx.with_git:
        ctx.report_optional(GIT, "skipped", "not requested (--without-git)")
        return
    if GIT in ctx.optional:
        return  # its file couldn't be fetched; the installer reported it already
    if not any(a.component == GIT for a in ctx.assets):
        ctx.report_optional(GIT, "skipped", "not in the pins")
        return
    dest = ctx.dir / "git"
    try:
        detail = _install(ctx, dest)
    except Exception as exc:  # noqa: BLE001 - optional: report it and go on without Git
        shutil.rmtree(dest, ignore_errors=True)
        ctx.executables.pop(EXECUTABLE, None)
        ctx.components.pop(GIT, None)
        detail = f"{type(exc).__name__}: {exc}"[-500:]
        ctx.report_optional(GIT, "failed", detail)
        _log(ctx, f"failed: {detail}")
        ctx.say(
            "warning: Git (optional) was not installed, so version history is unavailable; "
            f"everything else is installed. {detail}"
        )
        return
    ctx.report_optional(GIT, "installed", detail)
    _log(ctx, detail)


def _install(ctx: Context, dest: Path) -> str:
    asset = ctx.asset(GIT)
    exe_rel, core_rel = layout_for(ctx.platform)
    shutil.rmtree(dest, ignore_errors=True)
    extract(ctx.file(GIT), dest)
    git, core = dest / exe_rel, dest / core_rel
    if not git.is_file() or git.is_symlink():
        raise FileNotFoundError(f"the Git archive has no {exe_rel}")
    if not core.is_dir():
        raise FileNotFoundError(f"the Git archive has no {core_rel}")
    if not WINDOWS and not os.access(git, os.X_OK):
        raise PermissionError(f"{exe_rel} is not executable")
    done = proc.capture(
        [git, f"--exec-path={core}", "--version"], env=ctx.env(GIT_CONFIG_NOSYSTEM="1")
    )
    out = done.stdout.decode(errors="replace").strip()
    if done.returncode != 0 or not re.match(rf"git version {re.escape(asset.version)}\b", out):
        err = done.stderr.decode(errors="replace").strip()
        raise RuntimeError(f"Git reports {(out or err)[-200:]!r}, pinned {asset.version}")
    ctx.executables[EXECUTABLE] = git
    ctx.record(GIT, asset.version, [asset])
    return out


def _log(ctx: Context, line: str) -> None:
    try:
        with ctx.log("git").open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass
