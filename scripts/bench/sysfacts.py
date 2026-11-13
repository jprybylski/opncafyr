"""Coarse system facts for the interaction bench (#67): never paths or user names."""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


def _ps(command: str) -> str:
    root = os.environ.get("SYSTEMROOT", r"C:\Windows")
    exe = Path(root, "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
    try:
        out = subprocess.run(  # noqa: S603 - Windows PowerShell, a fixed script
            [str(exe), "-NoProfile", "-NonInteractive", "-Command", command],
            capture_output=True,
            text=True,
            timeout=60,
        )
        return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def _windows(folder: Path) -> dict[str, Any]:
    drive = folder.drive.rstrip(":\\") or "C"
    script = f"""
$ErrorActionPreference = 'SilentlyContinue'
$av = @(Get-CimInstance -Namespace root/SecurityCenter2 -ClassName AntiVirusProduct |
        ForEach-Object {{ $_.displayName }})
$mp = Get-MpComputerStatus
$vol = Get-Volume -DriveLetter {drive}
$part = Get-Partition -DriveLetter {drive}
$disk = if ($part) {{ Get-PhysicalDisk | Where-Object DeviceId -eq $part.DiskNumber }}
$cpu = Get-CimInstance Win32_Processor | Select-Object -First 1
[pscustomobject]@{{
  antivirus = $av
  defender_realtime = if ($mp) {{ [bool]$mp.RealTimeProtectionEnabled }} else {{ $null }}
  defender_mode = if ($mp) {{ [string]$mp.AMRunningMode }} else {{ $null }}
  filesystem = [string]$vol.FileSystem
  drive_type = [string]$vol.DriveType
  disk_media = if ($disk) {{ [string]$disk.MediaType }} else {{ $null }}
  disk_bus = if ($disk) {{ [string]$disk.BusType }} else {{ $null }}
  cpu = [string]$cpu.Name
}} | ConvertTo-Json -Compress
"""
    try:
        return json.loads(_ps(script) or "{}")
    except ValueError:
        return {}


def _linux(folder: Path) -> dict[str, Any]:
    out: dict[str, Any] = {"container": Path("/.dockerenv").exists()}
    try:
        best = ""
        for line in Path("/proc/mounts").read_text().splitlines():
            parts = line.split()
            if len(parts) >= 3 and str(folder).startswith(parts[1]) and len(parts[1]) >= len(best):
                best, out["filesystem"] = parts[1], parts[2]
    except OSError:
        pass
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.startswith("model name"):
                out["cpu"] = line.split(":", 1)[1].strip()
                break
    except OSError:
        pass
    return out


def _memory_gb() -> float | None:
    try:
        if sys.platform == "win32":
            import ctypes

            class MemStatus(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            m = MemStatus()
            m.dwLength = ctypes.sizeof(MemStatus)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
            return round(m.ullTotalPhys / 2**30, 1)
        return round(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 2**30, 1)
    except (AttributeError, OSError, ValueError):
        return None


def collect(folder: Path) -> dict[str, Any]:
    facts: dict[str, Any] = {
        "os": platform.platform(terse=True),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "cpus": os.cpu_count(),
        "memory_gb": _memory_gb(),
        "free_disk_gb": round(shutil.disk_usage(folder).free / 2**30, 1),
    }
    if sys.platform == "win32":
        facts |= _windows(folder)
    elif sys.platform.startswith("linux"):
        facts |= _linux(folder)
    return facts
