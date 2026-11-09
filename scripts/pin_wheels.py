"""Pin the exact wheel for every Python package the installer installs, per platform.

    uv run python scripts/pin_wheels.py            # rewrite runtime/python/wheels.toml
    uv run python scripts/pin_wheels.py --check    # exit 1 if the file is out of date

The installer (#19) runs under a bare Python with only the standard library, so it can't
evaluate environment markers or wheel tags. This dev tool does that once, from the two
locks, and writes ``runtime/python/wheels.toml``:

- ``[launcher.<platform>]``: the launcher's own environment (repository ``uv.lock``,
  without dev groups and without the project itself; the launcher comes from its wheel);
- ``[tools.<platform>]``: the tools environment (``runtime/python/tools/uv.lock``),
  deckifyr included.

Each entry names the package, version, wheel file, URL and SHA-256. Requirements come from
``uv export --frozen`` and markers are evaluated for CPython 3.13 on each platform, so
platform-only packages (``colorama`` on Windows) are right. For each package the most
specific wheel that fits is chosen. A package with no fitting wheel is an error: the
installer never builds from source. ``verify_pins.py`` runs ``--check``.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tomllib
import urllib.parse
from pathlib import Path

from packaging.markers import Marker

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "runtime" / "python" / "wheels.toml"
PLATFORMS = ("windows-x86_64", "macos-arm64", "macos-x86_64", "linux-x86_64", "linux-aarch64")
ENVS = {
    "launcher": (ROOT, ["--no-dev"]),
    "tools": (ROOT / "runtime" / "python" / "tools", []),
}
PY = (3, 13)
PY_FULL = "3.13.18"
MACOS_MIN = 15  # pins [platforms].macos_min: wheels built for a newer macOS won't load
GLIBC_MAX = (2, 39)  # pins [platforms].linux_glibc_min (#65): a wheel needing newer won't load
# Old manylinux names stand for a glibc version (PEP 600).
MANYLINUX_LEGACY = {"manylinux1": (2, 5), "manylinux2010": (2, 12), "manylinux2014": (2, 17)}
PLATFORM_TAG = {
    "windows-x86_64": re.compile(r"^win_amd64$"),
    "macos-arm64": re.compile(r"^macosx_(\d+)_(\d+)_(arm64|universal2)$"),
    "macos-x86_64": re.compile(r"^macosx_(\d+)_(\d+)_(x86_64|universal2|intel)$"),
    "linux-x86_64": re.compile(r"^(manylinux(?:1|2010|2014|_(\d+)_(\d+)))_x86_64$"),
    "linux-aarch64": re.compile(r"^(manylinux(?:2014|_(\d+)_(\d+)))_aarch64$"),
}
MARKER_ENV = {
    "windows-x86_64": {"sys_platform": "win32", "platform_machine": "AMD64", "os_name": "nt"},
    "macos-arm64": {"sys_platform": "darwin", "platform_machine": "arm64", "os_name": "posix"},
    "macos-x86_64": {"sys_platform": "darwin", "platform_machine": "x86_64", "os_name": "posix"},
    "linux-x86_64": {"sys_platform": "linux", "platform_machine": "x86_64", "os_name": "posix"},
    "linux-aarch64": {"sys_platform": "linux", "platform_machine": "aarch64", "os_name": "posix"},
}
PLATFORM_SYSTEM = {
    "windows-x86_64": "Windows",
    "macos-arm64": "Darwin",
    "macos-x86_64": "Darwin",
    "linux-x86_64": "Linux",
    "linux-aarch64": "Linux",
}


def wheel_score(filename: str, platform: str) -> float | None:
    """How well a wheel fits CPython 3.13 on ``platform`` (higher is better), or None."""
    parts = filename[:-4].split("-")
    py_tags, abi_tags, plat_tags = parts[-3].split("."), parts[-2].split("."), parts[-1]
    exact = f"cp{PY[0]}{PY[1]}"
    py_ok = any(
        t in ("py3", "py2.py3", exact)
        or (t.startswith("cp3") and "abi3" in abi_tags and int(t[3:]) <= PY[1])
        for t in py_tags
    )
    # Free-threaded builds (cp313t) don't load in the standard interpreter.
    if not py_ok or not any(t in ("none", "abi3", exact) for t in abi_tags):
        return None
    score = 2 if exact in py_tags else 1
    for tag in plat_tags.split("."):
        if tag == "any":
            return score
        m = PLATFORM_TAG[platform].match(tag)
        if not m:
            continue
        if platform.startswith("linux"):
            glibc = (int(m.group(2)), int(m.group(3))) if m.group(2) else None
            glibc = glibc or MANYLINUX_LEGACY[m.group(1)]
            if glibc <= GLIBC_MAX:
                return score + 10 + glibc[1] / 100  # the newest glibc the target has
        elif platform.startswith("windows") or int(m.group(1)) <= MACOS_MIN:
            return score + 10
    return None


def _export(project: Path, extra: list[str]) -> str:
    uv = shutil.which("uv")
    if uv is None:
        raise SystemExit("uv is required on PATH")
    argv = [uv, "export", "--frozen", "--no-emit-project", "--no-hashes", *extra]
    argv += ["--format", "requirements-txt", "--quiet"]
    return subprocess.run(  # noqa: S603 - dev tool, fixed argv
        argv, cwd=project, capture_output=True, text=True, check=True
    ).stdout


def needed(text: str, platform: str) -> list[str]:
    """Normalised package names required on ``platform``, from an exported requirements file."""
    env = {
        "python_version": f"{PY[0]}.{PY[1]}",
        "python_full_version": PY_FULL,
        "implementation_name": "cpython",
        "platform_python_implementation": "CPython",
        "platform_system": PLATFORM_SYSTEM[platform],
        "extra": "",
        **MARKER_ENV[platform],
    }
    names = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        req, _, marker = line.partition(";")
        if marker.strip() and not Marker(marker.strip()).evaluate(env):
            continue
        name = re.split(r"[\[=@ ]", req.strip(), maxsplit=1)[0]
        names.append(_norm(name))
    return sorted(set(names))


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def pin(env: str, platform: str) -> list[dict[str, str]]:
    project, extra = ENVS[env]
    lock = tomllib.loads((project / "uv.lock").read_text("utf-8"))
    by_name = {_norm(p["name"]): p for p in lock.get("package", [])}
    out = []
    for name in needed(_export(project, extra), platform):
        pkg = by_name[name]
        best = None
        for wheel in pkg.get("wheels", []):
            filename = urllib.parse.unquote(wheel["url"].rsplit("/", 1)[1])
            score = wheel_score(filename, platform)
            if score is not None and (best is None or score > best[0]):
                best = (score, filename, wheel)
        if best is None:
            raise SystemExit(f"{env}: no {platform} wheel for {pkg['name']} {pkg['version']}")
        _, filename, wheel = best
        algo, _, digest = wheel["hash"].partition(":")
        if algo != "sha256":
            raise SystemExit(f"{filename}: hash is {algo}, not sha256")
        out.append(
            {
                "name": pkg["name"],
                "version": pkg["version"],
                "file": filename,
                "url": wheel["url"],
                "sha256": digest,
            }
        )
    return out


def render() -> str:
    lines = [
        "# Generated by scripts/pin_wheels.py from uv.lock and runtime/python/tools/uv.lock.",
        "# Do not edit. One exact wheel per package and platform, for CPython 3.13 (#19, #36).",
        "",
        "schema_version = 1",
        f'python = "{PY_FULL}"',
    ]
    for env in ENVS:
        for platform in PLATFORMS:
            for w in pin(env, platform):
                lines += ["", f"[[{env}.{platform}]]"]
                lines += [f'{k} = "{w[k]}"' for k in ("name", "version", "file", "url", "sha256")]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="fail if wheels.toml is stale")
    args = parser.parse_args(argv)
    text = render()
    if args.check:
        current = OUT.read_text("utf-8") if OUT.is_file() else ""
        if current != text:
            print(f"{OUT.relative_to(ROOT)} is out of date: run scripts/pin_wheels.py")
            return 1
        print(f"{OUT.relative_to(ROOT)} is up to date")
        return 0
    OUT.write_text(text, "utf-8")
    print(f"wrote {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
