"""Which supported platform this is (ADR 0003): windows-x86_64, macos-arm64, macos-x86_64."""

from __future__ import annotations

import platform as _platform
import sys

from opncafyr.installer.verify import InstallError

SUPPORTED = ("windows-x86_64", "macos-arm64", "macos-x86_64")
MACOS_MIN = 15
WINDOWS_MIN_BUILD = 22000  # Windows 11


class UnsupportedPlatform(InstallError):
    pass


def detect() -> str:
    machine = _platform.machine().lower()
    if sys.platform == "darwin":
        # Under Rosetta, machine() says x86_64 on an arm64 Mac; install the native build.
        if machine == "arm64" or _sysctl("hw.optional.arm64") == "1":
            return "macos-arm64"
        if machine == "x86_64":
            return "macos-x86_64"
    if sys.platform == "win32" and machine in ("amd64", "x86_64"):
        return "windows-x86_64"
    raise UnsupportedPlatform(
        f"opncafyr supports Windows 11 x86_64 and macOS {MACOS_MIN}+ (arm64, x86_64); "
        f"this is {sys.platform}/{machine}."
    )


def check_os_version(platform: str) -> str:
    """Raise if the OS is older than the minimum; return a description of it."""
    if platform.startswith("macos"):
        release = _platform.mac_ver()[0] or "0"
        major = int(release.split(".")[0])
        if major < MACOS_MIN:
            raise UnsupportedPlatform(
                f"opncafyr needs macOS {MACOS_MIN} or later (opnca-r's binaries); "
                f"this Mac runs {release}."
            )
        return f"macOS {release}"
    version = sys.getwindowsversion() if sys.platform == "win32" else None  # type: ignore[attr-defined]
    if version is not None and version.build < WINDOWS_MIN_BUILD:
        raise UnsupportedPlatform(f"opncafyr needs Windows 11; this is build {version.build}.")
    return f"Windows build {version.build}" if version is not None else _platform.platform()


def _sysctl(name: str) -> str | None:
    try:
        import ctypes

        libc = ctypes.CDLL("/usr/lib/libc.dylib")
        value = ctypes.c_int(0)
        size = ctypes.c_size_t(ctypes.sizeof(value))
        if libc.sysctlbyname(name.encode(), ctypes.byref(value), ctypes.byref(size), None, 0):
            return None
        return str(value.value)
    except (OSError, AttributeError):
        return None
