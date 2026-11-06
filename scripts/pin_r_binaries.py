"""Pin every repository package in runtime/r/renv.lock per platform (#5).

    uv run python scripts/pin_r_binaries.py [--lock runtime/r/renv.lock] \\
        [--out runtime/r/binaries.toml] [--cache <dir>] [--jobs 8]

For each package and each supported platform (plus ``source``) it finds the file in the
dated Posit Package Manager snapshot named in the lockfile, checks that the snapshot has
exactly the locked version, downloads it and records its URL and SHA-256. The result is
what the installer and the offline bundle builder fetch: binaries only, never a build
from source on a user's machine.

Packages from the cellar (opnca, quartifyr, reportifyr, pyro) are pinned in
runtime/pins.toml instead.

Some P3M macOS builds link libraries a standard user can't install: P3M's gdtools links
XQuartz (/opt/X11), which needs an admin install. CRAN's own macOS build of the same
version is self-contained, so the packages in CRAN_MACOS come from CRAN for macOS
(``origin = "cran"``). CRAN keeps only the current version of a binary, so these URLs
stop working when the package is updated; the bundle builder (#36) keeps a copy. On
macOS, ``otool -L`` checks every downloaded macOS binary and the script fails if one
links anything outside /usr/lib, /System or R.framework.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import io
import json
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Repository paths for R 4.6. CRAN builds arm64 macOS binaries for macOS 14+ (sonoma).
PLATFORMS = {
    "macos-arm64": ("bin/macosx/sonoma-arm64/contrib/{minor}", ".tgz"),
    "macos-x86_64": ("bin/macosx/big-sur-x86_64/contrib/{minor}", ".tgz"),
    "windows-x86_64": ("bin/windows/contrib/{minor}", ".zip"),
    "source": ("src/contrib", ".tar.gz"),
}

USER_AGENT = "opncafyr-pin/1"

CRAN_MACOS = {"gdtools"}
CRAN_MACOS_PATHS = {
    "macos-arm64": "https://cran.r-project.org/bin/macosx/sonoma-arm64/contrib/{minor}",
    "macos-x86_64": "https://cran.r-project.org/bin/macosx/big-sur-x86_64/contrib/{minor}",
}
ALLOWED_LINKS = (
    "/usr/lib/",
    "/System/",
    "/Library/Frameworks/R.framework/",
    "@rpath",
    "@loader_path",
)


def _get(url: str) -> bytes:
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})  # noqa: S310
            with urllib.request.urlopen(req, timeout=120) as resp:  # noqa: S310
                return resp.read()
        except OSError:
            if attempt == 3:
                raise
            time.sleep(2**attempt)
    raise AssertionError("unreachable")


def parse_packages(text: str) -> dict[str, str]:
    """Map package name to version from a CRAN-like PACKAGES index."""
    out: dict[str, str] = {}
    for block in text.split("\n\n"):
        fields = {}
        for line in block.splitlines():
            if ":" in line and not line.startswith((" ", "\t")):
                key, _, value = line.partition(":")
                fields[key] = value.strip()
        if "Package" in fields and "Version" in fields:
            out[fields["Package"]] = fields["Version"]
    return out


def foreign_links(data: bytes) -> list[str]:
    """Shared libraries a macOS binary package links outside the system and R.

    Needs ``otool`` (macOS). Elsewhere it returns nothing, with a warning once.
    """
    otool = shutil.which("otool")
    if otool is None:
        if not getattr(foreign_links, "warned", False):
            print("otool not found: macOS link check skipped", file=sys.stderr)
            foreign_links.warned = True  # type: ignore[attr-defined]
        return []
    bad: list[str] = []
    with tempfile.TemporaryDirectory() as tmp, tarfile.open(fileobj=io.BytesIO(data)) as tar:
        members = [m for m in tar.getmembers() if m.isfile() and m.name.endswith(".so")]
        tar.extractall(tmp, members=members, filter="data")
        for member in members:
            out = subprocess.run(  # noqa: S603 - fixed tool, argv only
                [otool, "-L", f"{tmp}/{member.name}"], capture_output=True, text=True, check=True
            ).stdout.splitlines()[1:]
            for line in out:
                lib = line.strip().split(" (")[0]
                own = lib.rsplit("/", 1)[-1] == member.name.rsplit("/", 1)[-1]
                if lib and not own and not lib.startswith(ALLOWED_LINKS):
                    bad.append(f"{member.name} links {lib}")
    return bad


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lock", type=Path, default=ROOT / "runtime/r/renv.lock")
    parser.add_argument("--out", type=Path, default=ROOT / "runtime/r/binaries.toml")
    parser.add_argument("--cache", type=Path)
    parser.add_argument("--jobs", type=int, default=8)
    args = parser.parse_args()

    lock = json.loads(args.lock.read_text("utf-8"))
    minor = ".".join(lock["R"]["Version"].split(".")[:2])
    repo = next(r["URL"] for r in lock["R"]["Repositories"] if r["Name"] == "P3M")
    wanted = {
        name: entry["Version"]
        for name, entry in lock["Packages"].items()
        if entry.get("Source") == "Repository"
    }

    jobs: list[tuple[str, str, str]] = []
    problems: list[str] = []
    indexes: dict[str, dict[str, str]] = {}

    def index(base: str) -> dict[str, str]:
        if base not in indexes:
            indexes[base] = parse_packages(_get(f"{base}/PACKAGES").decode("utf-8"))
        return indexes[base]

    for platform, (path, ext) in PLATFORMS.items():
        for name, version in sorted(wanted.items()):
            base = f"{repo}/{path.format(minor=minor)}"
            if name in CRAN_MACOS and platform in CRAN_MACOS_PATHS:
                base = CRAN_MACOS_PATHS[platform].format(minor=minor)
            have = index(base).get(name)
            if have != version:
                problems.append(f"{platform}: {name} {version} not in {base} (has {have})")
                continue
            jobs.append((name, platform, f"{base}/{name}_{version}{ext}"))
    if problems:
        print("\n".join(problems), file=sys.stderr)
        return 1

    def fetch(job: tuple[str, str, str]) -> tuple[str, str, str, str]:
        name, platform, url = job
        cached = args.cache / platform / url.rsplit("/", 1)[1] if args.cache else None
        if cached and cached.is_file():
            data = cached.read_bytes()
        else:
            data = _get(url)
            if cached:
                cached.parent.mkdir(parents=True, exist_ok=True)
                cached.write_bytes(data)
        if platform.startswith("macos"):
            problems.extend(f"{platform}: {name}: {bad}" for bad in foreign_links(data))
        return name, platform, url, hashlib.sha256(data).hexdigest()

    results: dict[str, dict[str, tuple[str, str]]] = {}
    with concurrent.futures.ThreadPoolExecutor(args.jobs) as pool:
        for name, platform, url, sha in pool.map(fetch, jobs):
            results.setdefault(name, {})[platform] = (url, sha)
    if problems:
        print("\n".join(sorted(problems)), file=sys.stderr)
        return 1

    lines = [
        "# Generated by scripts/pin_r_binaries.py from runtime/r/renv.lock. Do not edit.",
        "# One entry per package and platform: the exact file in the dated P3M snapshot.",
        "",
        "schema_version = 1",
        f'repository = "{repo}"',
        f'r_minor = "{minor}"',
        "",
    ]
    for name in sorted(results):
        for platform in PLATFORMS:
            url, sha = results[name][platform]
            lines += [
                f'[packages."{name}".{platform}]',
                f'version = "{wanted[name]}"',
                f'url = "{url}"',
                f'sha256 = "{sha}"',
            ]
            if not url.startswith(repo):
                lines.append('origin = "cran"')
            lines.append("")
    args.out.write_text("\n".join(lines), "utf-8")
    print(f"{args.out}: {len(results)} packages x {len(PLATFORMS)} platforms")
    return 0


if __name__ == "__main__":
    sys.exit(main())
