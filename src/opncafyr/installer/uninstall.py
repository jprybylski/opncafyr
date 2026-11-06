"""Remove opncafyr for this user: app files, R, icons. Never a study workspace.

What goes: the icon(s), the macOS R root's ``versions/``, and the whole app root (installs,
cache, logs, state, the payload copy). Workspaces are folders the user chose elsewhere;
they are never under the app root, and the uninstaller refuses to run if the recent
workspaces list names one that is. Links are removed, never followed.

Refuses while a launcher is running (``--force`` overrides). The log goes to the system
temporary folder, since the app root's ``logs/`` is removed too. On Windows the wrapper
runs this from a copy of Python in ``%TEMP%``, because files in use can't be deleted.
"""

from __future__ import annotations

import json
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

from opncafyr.installer import icons
from opncafyr.installer.layout import Layout, remove_tree
from opncafyr.installer.verify import InstallError

Say = Callable[[str], None]


def _workspaces_inside(layout: Layout) -> list[str]:
    path = layout.state / "recent-workspaces.json"
    try:
        doc = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return []
    entries = doc.get("workspaces", doc.get("recent", [])) if isinstance(doc, dict) else []
    inside = []
    root = layout.root.resolve()
    for entry in entries:
        value = entry.get("path") if isinstance(entry, dict) else entry
        if not isinstance(value, str):
            continue
        try:
            p = Path(value).resolve()
        except OSError:
            continue
        if p == root or root in p.parents:
            inside.append(value)
    return inside


def uninstall(
    layout: Layout, *, force: bool, dry_run: bool, say: Say, leave: Path | None = None
) -> int:
    """``leave``: a folder under the app root to keep (the Windows wrapper's own folder,
    which the wrapper removes itself once cmd has finished reading ``install.bat``)."""
    log = Path(tempfile.gettempdir()) / f"opncafyr-uninstall-{time.strftime('%Y%m%d-%H%M%S')}.log"

    def note(message: str) -> None:
        say(message)
        with log.open("a", encoding="utf-8") as fh:
            fh.write(message + "\n")

    if layout.launcher_running() and not force:
        raise InstallError("opncafyr is running. Quit it from its page first (or pass --force).")
    inside = _workspaces_inside(layout)
    if inside:
        raise InstallError(
            "A workspace is inside the app folder, which uninstall removes: "
            + ", ".join(inside)
            + ". Move it elsewhere first."
        )
    targets = [*icons.existing()]
    if layout.r_root is not None and (layout.r_root / "versions").exists():
        targets.append(layout.r_root)
    if layout.root.exists():
        targets.append(layout.root)
    if dry_run:
        for t in targets:
            say(f"would remove {t}")
        say("dry run: nothing was changed")
        return 0
    note(f"uninstalling opncafyr from {layout.root}")
    try:
        icons.remove(layout, log)
        note("removed the icon(s)")
    except OSError as exc:
        note(f"warning: could not remove the icon(s): {exc}")
    if sys.platform == "win32":
        _remove_leftover_r_keys(layout, note)
    leftovers = []
    if layout.r_root is not None:
        try:
            remove_tree(layout.r_root / "versions")
            if layout.r_root.is_dir() and not any(layout.r_root.iterdir()):
                layout.r_root.rmdir()
            note(f"removed {layout.r_root}")
        except OSError as exc:
            leftovers.append(f"{layout.r_root}: {exc}")
    try:
        kept = _keep_under(layout.root, leave)
        if kept is None:
            remove_tree(layout.root)
            note(f"removed {layout.root}")
        else:
            for child in layout.root.iterdir():
                if child != kept:
                    remove_tree(child)
            note(f"removed {layout.root} (its installer folder goes when this window closes)")
    except OSError as exc:
        leftovers.append(f"{layout.root}: {exc}")
    if leftovers:
        for item in leftovers:
            note(f"could not remove {item}")
        note(f"log: {log}")
        return 1
    note(f"opncafyr is uninstalled. Log: {log}")
    return 0


def _keep_under(root: Path, leave: Path | None) -> Path | None:
    """The child of ``root`` that holds ``leave``, if ``leave`` is under ``root``."""
    if leave is None or not root.exists():
        return None
    try:
        rel = leave.resolve().relative_to(root.resolve())
    except ValueError:
        return None
    return root / rel.parts[0] if rel.parts else None


def _remove_leftover_r_keys(layout: Layout, note: Say) -> None:
    """Delete a per-user R uninstall entry only if it points into our app root.

    A finished install never leaves one (``r_registry`` restores the key); this catches an
    install that was killed while R's own installer ran.
    """
    import winreg

    from opncafyr.installer import r_registry

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r_registry.UNINSTALL) as handle:
            names = []
            i = 0
            while True:
                try:
                    names.append(winreg.EnumKey(handle, i))
                except OSError:
                    break
                i += 1
    except FileNotFoundError:
        return
    for name in names:
        if not (name.startswith("R for Windows ") and name.endswith("_is1")):
            continue
        key = rf"{r_registry.UNINSTALL}\{name}"
        if r_registry.owned_by(key, str(layout.root)):
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key)
            note(f"removed the leftover registry entry {name}")
