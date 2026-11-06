"""The icon a user clicks: a minimal ``.app`` on macOS, ``.lnk`` shortcuts on Windows.

- **macOS:** ``~/Applications/opncafyr.app`` with an ``Info.plist`` and a shell stub that
  starts ``<app root>/current/launcher/bin/python -m opncafyr launch`` in the background
  and exits, so the app never sits in the Dock as "not responding". It goes through the
  ``current`` link, so an update needs no new app. Locally created, it carries no
  quarantine flag; signing and notarization are #39.
- **Windows:** ``opncafyr.lnk`` on the Desktop and in the Start Menu, made through
  ``WScript.Shell`` by ``ps/icons_windows.ps1``. A shortcut can't follow the pointer
  file, so it names the active install's ``pythonw.exe`` and is rewritten on activation.
"""

from __future__ import annotations

import os
import plistlib
import shlex
import sys
from importlib import resources
from pathlib import Path

from opncafyr.installer import proc
from opncafyr.installer.layout import Layout

APP_NAME = "opncafyr"
APPS_DIR_ENV = "OPNCAFYR_APPS_DIR"  # tests and CI only
SHORTCUTS_ENV = "OPNCAFYR_SHORTCUT_DIRS"  # tests and CI only: ;-separated folders


def macos_app_path(environ: dict[str, str] | None = None) -> Path:
    env = os.environ if environ is None else environ
    base = Path(env[APPS_DIR_ENV]) if env.get(APPS_DIR_ENV) else Path.home() / "Applications"
    return base / f"{APP_NAME}.app"


def write_macos_app(layout: Layout, version: str, app: Path | None = None) -> Path:
    app = app or macos_app_path()
    contents = app / "Contents"
    (contents / "MacOS").mkdir(parents=True, exist_ok=True)
    plist = {
        "CFBundleExecutable": APP_NAME,
        "CFBundleIdentifier": "org.opncafyr.launcher",
        "CFBundleName": APP_NAME,
        "CFBundleDisplayName": APP_NAME,
        "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": version,
        "CFBundleVersion": version,
        "LSMinimumSystemVersion": "15.0",
        "NSHighResolutionCapable": True,
    }
    with (contents / "Info.plist").open("wb") as fh:
        plistlib.dump(plist, fh)
    root = shlex.quote(str(layout.root))
    stub = contents / "MacOS" / APP_NAME
    stub.write_text(
        "#!/bin/sh\n"
        "# opncafyr launcher icon (written by the installer). Starts the launcher of the\n"
        "# active install, which opens the browser, and exits.\n"
        f"ROOT={root}\n"
        'PY="$ROOT/current/launcher/bin/python"\n'
        'if [ ! -x "$PY" ]; then\n'
        '  /usr/bin/osascript -e \'display alert "opncafyr is not installed correctly" '
        'message "Run the installer again to repair it."\' >/dev/null 2>&1\n'
        "  exit 1\n"
        "fi\n"
        'mkdir -p "$ROOT/logs"\n'
        'nohup "$PY" -m opncafyr launch >> "$ROOT/logs/launcher-app.log" 2>&1 &\n'
        "exit 0\n",
        "utf-8",
    )
    stub.chmod(0o755)
    return app


def windows_shortcut_dirs(environ: dict[str, str] | None = None) -> list[Path]:
    env = os.environ if environ is None else environ
    if env.get(SHORTCUTS_ENV):
        return [Path(p) for p in env[SHORTCUTS_ENV].split(";") if p]
    appdata = env.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    start_menu = Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
    return [start_menu, Path("<Desktop>")]


def _powershell() -> str:
    root = os.environ.get("SYSTEMROOT", r"C:\Windows")
    return str(Path(root) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe")


def _run_ps(action: str, layout: Layout, log: Path, **values: str) -> str:
    script = layout.cache / "icons_windows.ps1"
    script.parent.mkdir(parents=True, exist_ok=True)
    text = resources.files("opncafyr.installer").joinpath("ps", "icons_windows.ps1").read_text()
    script.write_text(text, "utf-8")
    env = proc.base_env(offline=True)
    # Values go through the environment: PowerShell 5.1 mangles quotes in arguments.
    env |= {f"OPNCAFYR_ICON_{k.upper()}": v for k, v in values.items()}
    env["OPNCAFYR_ICON_ACTION"] = action
    env["OPNCAFYR_ICON_DIRS"] = ";".join(str(d) for d in windows_shortcut_dirs())
    done = proc.run(
        [_powershell(), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass"]
        + ["-File", script],
        env=env,
        log=log,
    )
    del done
    return log.read_text("utf-8", errors="replace")


def create(layout: Layout, install_id: str, version: str, log: Path) -> list[Path]:
    """Create or update the icon(s) for the active install."""
    if sys.platform == "darwin":
        return [write_macos_app(layout, version)]
    if sys.platform == "win32":
        vdir = layout.version_dir(install_id)
        _run_ps(
            "create",
            layout,
            log,
            target=str(vdir / "launcher" / "Scripts" / "pythonw.exe"),
            arguments="-m opncafyr launch",
            workdir=str(vdir),
            description="opncafyr workspace launcher",
        )
        return existing()
    return []


def existing() -> list[Path]:
    if sys.platform == "darwin":
        app = macos_app_path()
        return [app] if app.exists() else []
    if sys.platform == "win32":
        found = []
        for d in windows_shortcut_dirs():
            if str(d) == "<Desktop>":
                d = _desktop()
            lnk = d / f"{APP_NAME}.lnk"
            if lnk.exists():
                found.append(lnk)
        return found
    return []


def present(layout: Layout) -> bool:
    if sys.platform == "darwin":
        stub = macos_app_path() / "Contents" / "MacOS" / APP_NAME
        return stub.is_file() and str(layout.root) in stub.read_text("utf-8")
    if sys.platform == "win32":
        return len(existing()) == len(windows_shortcut_dirs())
    return True


def remove(layout: Layout, log: Path) -> None:
    if sys.platform == "darwin":
        from opncafyr.installer.layout import remove_tree

        remove_tree(macos_app_path())
    elif sys.platform == "win32":
        _run_ps("remove", layout, log)


def _desktop() -> Path:
    try:
        import ctypes
        from ctypes import wintypes

        buf = ctypes.create_unicode_buffer(wintypes.MAX_PATH)
        ctypes.windll.shell32.SHGetFolderPathW(None, 0x0010, None, 0, buf)  # CSIDL_DESKTOPDIRECTORY
        return Path(buf.value)
    except (AttributeError, OSError):
        return Path.home() / "Desktop"
