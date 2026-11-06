"""Native folder picker, run by the controller rather than the browser (#17).

Browser directory access differs between browsers and never yields a real path, so the
controller shows the operating system's own dialog:

- macOS: ``/usr/bin/osascript`` running a fixed AppleScript ``choose folder``;
- Windows: Windows PowerShell running a fixed ``FolderBrowserDialog`` script.

The scripts are constants. The prompt and the start folder reach them as data (AppleScript
``argv`` and environment variables), never by building script text, and the commands
are argv arrays with ``shell=False``. Both dialogs can create a new folder.
"""

from __future__ import annotations

import base64
import os
import subprocess  # noqa: S404 - fixed argv arrays only
import sys
from collections.abc import Callable
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

PICKER_TIMEOUT_S = 600
OSASCRIPT = "/usr/bin/osascript"

# AppleScript user-cancel error number.
_MAC_CANCELLED = "-128"
MAC_SCRIPT = (
    "on run argv",
    "activate",
    "if (count of argv) > 1 then",
    "return POSIX path of (choose folder with prompt (item 1 of argv)"
    " default location ((item 2 of argv) as POSIX file))",
    "end if",
    "return POSIX path of (choose folder with prompt (item 1 of argv))",
    "end run",
)

WINDOWS_SCRIPT = "\n".join(
    (
        "$ErrorActionPreference = 'Stop'",
        "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8",
        "Add-Type -AssemblyName System.Windows.Forms",
        "[System.Windows.Forms.Application]::EnableVisualStyles()",
        "$owner = New-Object System.Windows.Forms.Form",
        "$owner.TopMost = $true",
        "$owner.ShowInTaskbar = $false",
        "$dialog = New-Object System.Windows.Forms.FolderBrowserDialog",
        "$dialog.Description = $env:PICKER_PROMPT",
        "$dialog.ShowNewFolderButton = $true",
        "$start = $env:PICKER_START",
        "if ($start -and (Test-Path -LiteralPath $start -PathType Container)) {",
        "  $dialog.SelectedPath = $start",
        "}",
        "$result = $dialog.ShowDialog($owner)",
        "$owner.Dispose()",
        "if ($result -eq [System.Windows.Forms.DialogResult]::OK) {",
        "  [Console]::Out.Write($dialog.SelectedPath)",
        "}",
    )
)

Runner = Callable[..., subprocess.CompletedProcess[Any]]


class PickerUnavailable(RuntimeError):
    """This platform has no supported native folder picker."""


class PickerError(RuntimeError):
    """The picker could not be shown or returned something unusable."""


def mac_argv(prompt: str, start: Path | None) -> list[str]:
    argv = [OSASCRIPT]
    for line in MAC_SCRIPT:
        argv += ["-e", line]
    argv += ["--", prompt]
    if start is not None:
        argv.append(str(start))
    return argv


def windows_powershell() -> str:
    """Absolute path of Windows PowerShell, from the system directory (not PATH)."""
    import ctypes

    buf = ctypes.create_unicode_buffer(260)
    if not ctypes.windll.kernel32.GetSystemDirectoryW(buf, len(buf)):  # type: ignore[attr-defined]
        raise PickerError("Could not find the Windows system folder.")
    return str(Path(buf.value) / "WindowsPowerShell" / "v1.0" / "powershell.exe")


def windows_argv(powershell: str) -> list[str]:
    encoded = base64.b64encode(WINDOWS_SCRIPT.encode("utf-16-le")).decode("ascii")
    return [
        powershell,
        "-NoProfile",
        "-NonInteractive",
        "-STA",
        "-ExecutionPolicy",
        "Bypass",
        "-EncodedCommand",
        encoded,
    ]


def _clean(text: str, platform: str) -> Path | None:
    text = text.strip("\r\n")
    if not text:
        return None
    flavour = PureWindowsPath if platform == "win32" else PurePosixPath
    if not flavour(text).is_absolute():
        raise PickerError("The folder picker returned an unusable path.")
    # osascript ends folder paths with "/". Keep the root ("/", "D:\\") as it is.
    if text.endswith(("/", "\\")) and str(flavour(text).parent) != str(flavour(text)):
        text = text[:-1]
    return Path(text)


def pick_folder(
    prompt: str,
    start: Path | None = None,
    *,
    platform: str | None = None,
    run: Runner = subprocess.run,
    powershell: Callable[[], str] = windows_powershell,
) -> Path | None:
    """Show the native folder picker. Returns the chosen folder, or None on cancel."""
    platform = platform or sys.platform
    if start is not None and not start.is_dir():
        start = None
    common: dict[str, Any] = {
        "shell": False,
        "capture_output": True,
        "timeout": PICKER_TIMEOUT_S,
        "check": False,
    }
    try:
        if platform == "darwin":
            proc = run(mac_argv(prompt, start), **common)
            if proc.returncode != 0:
                stderr = proc.stderr.decode("utf-8", "replace")
                if _MAC_CANCELLED in stderr:
                    return None
                raise PickerError("The folder picker failed to open.")
        elif platform == "win32":
            env = dict(os.environ, PICKER_PROMPT=prompt, PICKER_START=str(start or ""))
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            proc = run(windows_argv(powershell()), env=env, creationflags=flags, **common)
            if proc.returncode != 0:
                raise PickerError("The folder picker failed to open.")
        else:
            raise PickerUnavailable("This platform has no native folder picker.")
    except subprocess.TimeoutExpired:
        raise PickerError("The folder picker was open too long and was closed.") from None
    except OSError:
        raise PickerError("The folder picker could not be started.") from None
    return _clean(proc.stdout.decode("utf-8", "replace"), platform)
