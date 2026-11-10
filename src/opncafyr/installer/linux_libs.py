"""The shared libraries a native Linux install needs, checked before anything is installed (#65).

The install runs as a standard user, with no apt, no root and no compiler, so it can't add a
system library. It checks that each one is present and, when some are missing, says exactly
what to ask an administrator for. Nothing is bundled and nothing is installed here.

The list is every library the pinned R, its 126 packages and the Python wheels link directly
(``DT_NEEDED``), found by reading their ELF headers on Ubuntu 24.04 for both architectures:
the same 36 on x86_64 and aarch64. Libraries only R's X11 and Tcl/Tk modules need
(libX11, libXt, libtcl8.6, libtk8.6) are left out: opncafyr never loads those modules.
``glibc`` itself is checked by ``platforms.check_linux``.

``apt`` is the Ubuntu package that provides the library, for the administrator's benefit. The
installer never runs apt.
"""

from __future__ import annotations

import os
import platform as _platform
import shutil
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from opncafyr.installer import proc
from opncafyr.installer.verify import InstallError


@dataclass(frozen=True)
class Lib:
    soname: str
    apt: str


REQUIRED = (
    Lib("libblas.so.3", "libopenblas0-pthread"),
    Lib("liblapack.so.3", "libopenblas0-pthread"),
    Lib("libbz2.so.1.0", "libbz2-1.0"),
    Lib("libcairo.so.2", "libcairo2"),
    Lib("libcrypto.so.3", "libssl3t64"),
    Lib("libssl.so.3", "libssl3t64"),
    Lib("libcurl.so.4", "libcurl4t64"),
    Lib("libdeflate.so.0", "libdeflate0"),
    Lib("libfontconfig.so.1", "libfontconfig1"),
    Lib("libfreetype.so.6", "libfreetype6"),
    Lib("libfribidi.so.0", "libfribidi0"),
    Lib("libgcc_s.so.1", "libgcc-s1"),
    Lib("libgfortran.so.5", "libgfortran5"),
    Lib("libgit2.so.1.7", "libgit2-1.7"),
    Lib("libgobject-2.0.so.0", "libglib2.0-0t64"),
    Lib("libgomp.so.1", "libgomp1"),
    Lib("libharfbuzz.so.0", "libharfbuzz0b"),
    Lib("libicui18n.so.74", "libicu74"),
    Lib("libicuuc.so.74", "libicu74"),
    Lib("libjpeg.so.8", "libjpeg-turbo8"),
    Lib("liblzma.so.5", "liblzma5"),
    Lib("libpango-1.0.so.0", "libpango-1.0-0"),
    Lib("libpangocairo-1.0.so.0", "libpangocairo-1.0-0"),
    Lib("libpcre2-8.so.0", "libpcre2-8-0"),
    Lib("libpng16.so.16", "libpng16-16t64"),
    Lib("libreadline.so.8", "libreadline8t64"),
    Lib("libsodium.so.23", "libsodium23"),
    Lib("libstdc++.so.6", "libstdc++6"),
    Lib("libtiff.so.6", "libtiff6"),
    Lib("libtirpc.so.3", "libtirpc3t64"),
    Lib("libuv.so.1", "libuv1t64"),
    Lib("libwebp.so.7", "libwebp7"),
    Lib("libwebpmux.so.3", "libwebpmux3"),
    Lib("libxml2.so.2", "libxml2"),
    Lib("libz.so.1", "zlib1g"),
    Lib("libzstd.so.1", "libzstd1"),
)

TRIPLET = {"x86_64": "x86_64-linux-gnu", "aarch64": "aarch64-linux-gnu"}
LDCONFIG_PATHS = ("/sbin/ldconfig", "/usr/sbin/ldconfig", "/usr/bin/ldconfig")
FONT_DIRS = ("/usr/share/fonts", "/usr/local/share/fonts", "/usr/X11R6/lib/X11/fonts")


class MissingLibraries(InstallError):
    def __init__(self, missing: list[Lib]) -> None:
        self.missing = missing
        packages = sorted({lib.apt for lib in missing})
        super().__init__(
            "This computer is missing shared libraries opncafyr needs, and the installer can't "
            "add them without administrator rights.\n"
            f"  Missing: {', '.join(sorted(lib.soname for lib in missing))}\n"
            "  Ask your administrator to install these Ubuntu packages, then run the installer "
            f"again:\n    {' '.join(packages)}"
        )


def _ldconfig() -> str | None:
    found = shutil.which("ldconfig", path=proc.system_path())
    if found:
        return found
    return next((p for p in LDCONFIG_PATHS if os.access(p, os.X_OK)), None)


def known_sonames() -> set[str] | None:
    """Sonames the dynamic linker knows (``ldconfig -p``), or None if it can't be asked."""
    ldconfig = _ldconfig()
    if ldconfig is None:
        return None
    try:
        out = proc.capture([ldconfig, "-p"], env=proc.base_env(offline=True), timeout=30)
    except (OSError, RuntimeError, ValueError):
        return None
    if out.returncode != 0:
        return None
    text = out.stdout.decode("utf-8", "replace")
    return {line.split()[0] for line in text.splitlines() if " => " in line}


def _on_disk(soname: str, machine: str) -> bool:
    triplet = TRIPLET.get(machine, "")
    roots = [f"/lib/{triplet}", f"/usr/lib/{triplet}", "/lib64", "/usr/lib64", "/usr/local/lib"]
    return any((Path(root) / soname).exists() for root in roots)


def missing_libraries(
    required: Iterable[Lib] = REQUIRED,
    *,
    known: set[str] | None = None,
    machine: str | None = None,
) -> list[Lib]:
    """The required libraries that are not installed. ``known`` is for tests."""
    machine = machine or _platform.machine().lower()
    if known is None:
        known = known_sonames()
    if known is not None:
        return [lib for lib in required if lib.soname not in known]
    return [lib for lib in required if not _on_disk(lib.soname, machine)]


def check_libraries(**kw) -> None:
    missing = missing_libraries(**kw)
    if missing:
        raise MissingLibraries(missing)


def has_fonts(dirs: Iterable[str] = FONT_DIRS) -> bool:
    """Whether any font file is installed. fontconfig alone is not enough for text in a DOCX."""
    for root in dirs:
        base = Path(root)
        if base.is_dir() and any(
            p.suffix.lower() in (".ttf", ".otf", ".pfb") for p in base.rglob("*") if p.is_file()
        ):
            return True
    return False
