"""Windows: keep R's per-user installer away from the user's own R registration.

``R-<version>-win.exe /CURRENTUSER`` always writes
``HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\R for Windows <version>_is1``,
a key every per-user R of that version shares (docs/spikes/windows-install.md, finding 1).
The installer snapshots the key before running R's installer and restores it afterwards
(or deletes it if there was none), so opncafyr's private R never appears there and a
user's own R 4.6.2 keeps its entry. With ``!recordversion`` R writes nothing under
``HKCU\\Software\\R-core``.
"""

from __future__ import annotations

import contextlib
import sys
from typing import Any

UNINSTALL = r"Software\Microsoft\Windows\CurrentVersion\Uninstall"

Snapshot = list[tuple[str, Any, int]] | None


def uninstall_key(r_version: str) -> str:
    return rf"{UNINSTALL}\R for Windows {r_version}_is1"


def _winreg() -> Any:
    if sys.platform != "win32":
        raise RuntimeError("the Windows registry exists on Windows only")
    import winreg

    return winreg


def snapshot(key: str, winreg: Any = None) -> Snapshot:
    """The key's values, or None if it doesn't exist."""
    winreg = winreg or _winreg()
    try:
        handle = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key, 0, winreg.KEY_READ)
    except FileNotFoundError:
        return None
    values = []
    with handle:
        i = 0
        while True:
            try:
                values.append(winreg.EnumValue(handle, i))
            except OSError:
                break
            i += 1
    return values


def restore(key: str, saved: Snapshot, winreg: Any = None) -> None:
    """Put the key back as it was (deleting it if it didn't exist)."""
    winreg = winreg or _winreg()
    with contextlib.suppress(FileNotFoundError):
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key)
    if saved is None:
        return
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key) as handle:
        for name, value, kind in saved:
            winreg.SetValueEx(handle, name, 0, kind, value)


def owned_by(key: str, root: str, winreg: Any = None) -> bool:
    """True if the key points into ``root`` (a leftover from an interrupted install)."""
    values = snapshot(key, winreg)
    if not values:
        return False
    location = next((v for n, v, _ in values if n == "InstallLocation"), "")
    return bool(location) and str(location).lower().startswith(root.lower())
