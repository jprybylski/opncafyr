"""Which supported platform this is (ADR 0003): Windows 11 x86_64, macOS arm64 and x86_64, and
Ubuntu 24.04 x86_64 and aarch64 (the #65 amendment)."""

from __future__ import annotations

import os
import platform as _platform
import sys
from collections.abc import Mapping

from opncafyr.installer.verify import InstallError

SUPPORTED = ("windows-x86_64", "macos-arm64", "macos-x86_64", "linux-x86_64", "linux-aarch64")
MACOS_MIN = 15
WINDOWS_MIN_BUILD = 22000  # Windows 11
# The one supported Linux (pins [platforms].linux_*; a test keeps these equal). Posit's R and
# its noble R binaries are built on Ubuntu 24.04, so glibc 2.39 is the floor.
LINUX_OS = "ubuntu"
LINUX_OS_VERSION = "24.04"
LINUX_GLIBC_MIN = (2, 39)
LINUX_MACHINES = {
    "x86_64": "linux-x86_64",
    "amd64": "linux-x86_64",
    "aarch64": "linux-aarch64",
    "arm64": "linux-aarch64",
}


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
    if sys.platform.startswith("linux") and machine in LINUX_MACHINES:
        return LINUX_MACHINES[machine]
    raise UnsupportedPlatform(
        f"opncafyr supports Windows 11 x86_64, macOS {MACOS_MIN}+ (arm64, x86_64) and "
        f"Ubuntu {LINUX_OS_VERSION} (x86_64, aarch64); this is {sys.platform}/{machine}."
    )


def opnca_name(platform: str) -> tuple[str, str]:
    """(platform, arch) as opnca-r's ``manifest.json`` spells them: ``linux-aarch64`` is
    ``linux`` / ``arm64`` there, and its file names say ``linux-arm64`` (#65)."""
    plat, _, arch = platform.partition("-")
    return plat, "arm64" if arch == "aarch64" else arch


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
    if platform.startswith("linux"):
        return check_linux()
    version = sys.getwindowsversion() if sys.platform == "win32" else None  # type: ignore[attr-defined]
    if version is not None and version.build < WINDOWS_MIN_BUILD:
        raise UnsupportedPlatform(f"opncafyr needs Windows 11; this is build {version.build}.")
    return f"Windows build {version.build}" if version is not None else _platform.platform()


def os_release(path: str = "/etc/os-release") -> Mapping[str, str]:
    """``/etc/os-release`` as a dict (empty when there is none)."""
    out: dict[str, str] = {}
    try:
        text = open(path, encoding="utf-8").read()  # noqa: SIM115 - one small file
    except OSError:
        return out
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if sep and key.strip() and not key.startswith("#"):
            out[key.strip()] = value.strip().strip("\"'")
    return out


def glibc_version() -> tuple[int, int] | None:
    """The C library's version, or None if this isn't glibc."""
    try:
        text = os.confstr("CS_GNU_LIBC_VERSION") or ""
    except (AttributeError, ValueError, OSError):
        return None
    name, _, version = text.partition(" ")
    if name != "glibc":
        return None
    parts = version.split(".")
    try:
        return int(parts[0]), int(parts[1])
    except (IndexError, ValueError):
        return None


_HOST = object()


def check_linux(
    release: Mapping[str, str] | None = None,
    glibc: tuple[int, int] | None | object = _HOST,
) -> str:
    """Raise unless this is Ubuntu 24.04 with glibc 2.39 or later; return a description.

    ``release`` and ``glibc`` are for tests; by default they come from this computer.
    """
    info = os_release() if release is None else release
    version = glibc_version() if glibc is _HOST else glibc
    name, ver = info.get("ID", ""), info.get("VERSION_ID", "")
    if name != LINUX_OS or ver != LINUX_OS_VERSION:
        found = info.get("PRETTY_NAME") or f"{name or 'an unknown Linux'} {ver}".strip()
        raise UnsupportedPlatform(
            f"opncafyr's native Linux install supports Ubuntu {LINUX_OS_VERSION} only; "
            f"this is {found}. The container works on any Linux with Docker or Podman."
        )
    if not isinstance(version, tuple):
        raise UnsupportedPlatform(
            "opncafyr needs glibc "
            f"{LINUX_GLIBC_MIN[0]}.{LINUX_GLIBC_MIN[1]} or later; this computer's C library "
            "could not be identified."
        )
    if version < LINUX_GLIBC_MIN:
        raise UnsupportedPlatform(
            f"opncafyr needs glibc {LINUX_GLIBC_MIN[0]}.{LINUX_GLIBC_MIN[1]} or later; "
            f"this computer has glibc {version[0]}.{version[1]}."
        )
    return f"Ubuntu {ver}, glibc {version[0]}.{version[1]}"


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
