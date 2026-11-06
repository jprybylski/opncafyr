"""List every pinned file an install on one platform needs (install spikes #6, #7).

    uv run python scripts/spikes/spike_assets.py --platform windows-x86_64 > assets.json

Prints JSON: a list of ``{"kind", "name", "url", "sha256", "local_name"}`` taken from
runtime/pins.toml, runtime/r/binaries.toml and runtime/python/tools/uv.lock:
uv, Python, R, Quarto, the opnca-r binary, the cellar tarballs (quartifyr, reportifyr,
pyro), the deckifyr wheel, every R package binary for the platform and every wheel of the
tools environment that matches the platform. Spike and installer scripts fetch these
(online) or read them from a directory (offline) and check each SHA-256.

Spike tooling only; installer v1 (#19) does this for real.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tomllib
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLATFORMS = ("windows-x86_64", "macos-arm64", "macos-x86_64")
WHEEL_PLATFORM = {
    "windows-x86_64": re.compile(r"^win_amd64$"),
    "macos-arm64": re.compile(r"^macosx_(\d+)_(\d+)_(arm64|universal2)$"),
    "macos-x86_64": re.compile(r"^macosx_(\d+)_(\d+)_(x86_64|universal2|intel)$"),
}
MACOS_MAX = 15  # oldest supported macOS (pins [platforms].macos_min)
PY = (3, 13)


def _wheel_ok(filename: str, platform: str) -> bool:
    parts = filename[:-4].split("-")
    py_tags, abi_tags, plat_tags = parts[-3].split("."), parts[-2].split("."), parts[-1]
    py_ok = any(
        t in ("py3", f"cp{PY[0]}{PY[1]}", "py2.py3")
        or (t.startswith("cp3") and "abi3" in abi_tags and int(t[3:]) <= PY[1])
        for t in py_tags
    )
    # Free-threaded builds (cp313t) don't load in the standard interpreter.
    abi_ok = any(t in ("none", "abi3", f"cp{PY[0]}{PY[1]}") for t in abi_tags)
    if not py_ok or not abi_ok:
        return False
    for tag in plat_tags.split("."):
        if tag == "any":
            return True
        m = WHEEL_PLATFORM[platform].match(tag)
        if m and (platform.startswith("windows") or int(m.group(1)) <= MACOS_MAX):
            return True
    return False


ENVIRONMENTS = {
    "windows-x86_64": {"sys_platform": "win32", "platform_machine": "AMD64", "os_name": "nt"},
    "macos-arm64": {"sys_platform": "darwin", "platform_machine": "arm64", "os_name": "posix"},
    "macos-x86_64": {"sys_platform": "darwin", "platform_machine": "x86_64", "os_name": "posix"},
}


def _needed(platform: str) -> set[tuple[str, str]]:
    """(name, version) pairs the tools environment installs on this platform.

    Uses ``uv export`` (from PATH) and evaluates each requirement's marker for the
    target platform, so platform-only packages (e.g. onnxruntime versions) are right.
    """
    from packaging.markers import Marker

    uv = shutil.which("uv")
    if uv is None:
        raise SystemExit("uv is required on PATH")
    text = subprocess.run(  # noqa: S603 - fixed tool, argv only
        [
            uv,
            "export",
            "--frozen",
            "--no-emit-project",
            "--no-hashes",
            "--format",
            "requirements-txt",
        ],
        cwd=ROOT / "runtime/python/tools",
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    env = {
        "python_version": f"{PY[0]}.{PY[1]}",
        "python_full_version": "3.13.18",
        "implementation_name": "cpython",
        "platform_python_implementation": "CPython",
        "extra": "",
        **ENVIRONMENTS[platform],
    }
    out = set()
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        req, _, marker = line.partition(";")
        if marker.strip() and not Marker(marker.strip()).evaluate(env):
            continue
        req = req.strip()
        if " @ " in req:
            name = req.split("[")[0].split(" @ ")[0]
            out.add((name.lower(), "*"))
        else:
            name, _, version = req.partition("==")
            out.add((name.split("[")[0].lower(), version.strip()))
    return out


def wheels(platform: str) -> list[dict[str, str]]:
    lock = tomllib.loads((ROOT / "runtime/python/tools/uv.lock").read_text("utf-8"))
    needed = _needed(platform)
    out = []
    for pkg in lock.get("package", []):
        key = pkg["name"].lower()
        if (key, pkg.get("version", "")) not in needed and (key, "*") not in needed:
            continue
        candidates = [w for w in pkg.get("wheels", []) if _wheel_ok(_name(w["url"]), platform)]
        if not candidates:
            # Reported, not fatal: e.g. pymupdf 2.1 has no macOS x86_64 wheel.
            print(
                f"WARNING no {platform} wheel for {pkg['name']} {pkg['version']}", file=sys.stderr
            )
            out.append(
                {
                    "kind": "missing-wheel",
                    "name": pkg["name"],
                    "url": "",
                    "sha256": "",
                    "local_name": f"{pkg['name']}-{pkg['version']}",
                }
            )
            continue
        # Prefer a platform wheel over a pure one when both exist.
        candidates.sort(key=lambda w: _name(w["url"]).endswith("-any.whl"))
        w = candidates[0]
        out.append(_entry("wheel", pkg["name"], w["url"], w["hash"].split(":", 1)[1]))
    return out


def _name(url: str) -> str:
    return urllib.parse.unquote(url.rsplit("/", 1)[1])


def _entry(kind: str, name: str, url: str, sha: str, local: str | None = None) -> dict[str, str]:
    return {
        "kind": kind,
        "name": name,
        "url": url,
        "sha256": sha,
        "local_name": local or _name(url),
    }


def assets(platform: str) -> list[dict[str, str]]:
    pins = tomllib.loads((ROOT / "runtime/pins.toml").read_text("utf-8"))
    out = []
    for section in ("uv", "python", "r", "quarto"):
        a = pins[section]["assets"][platform]
        out.append(_entry(section, section, a["url"], a["sha256"]))
    for pkg, entry in pins["packages"].items():
        for filename, sha in entry["assets"].items():
            if pkg == "opnca-r":
                if filename != "manifest.json" and f"_{platform}." not in filename:
                    continue
            elif pkg == "deckifyr":
                if filename != entry["wheel"]:
                    continue
            elif not filename.endswith(".tar.gz"):
                continue
            out.append(_entry(f"package:{pkg}", pkg, entry["release_url"] + filename, sha))
    binaries = tomllib.loads((ROOT / "runtime/r/binaries.toml").read_text("utf-8"))
    for name, plats in sorted(binaries["packages"].items()):
        b = plats[platform]
        out.append(_entry("r-binary", name, b["url"], b["sha256"]))
    return out + wheels(platform)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--platform", required=True, choices=PLATFORMS)
    args = parser.parse_args()
    json.dump(assets(args.platform), sys.stdout, indent=1)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
