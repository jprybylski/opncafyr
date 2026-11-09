"""Online phase of the offline CI job (#44): fetch every pinned asset into one folder.

    python scripts/offline/fetch.py --out <assets> [--platform linux-x86_64|linux-aarch64]
        [--opnca-vendored <file> | --opnca-release]

Everything comes from ``runtime/pins.toml`` and the files it points to, and every file is
checked against its pinned SHA-256 before it is kept. The offline phase
(``offline_phase.sh``) then installs and runs from ``<assets>`` with the network cut.

Layout of ``<assets>``:

- ``runtime/``: uv, python-build-standalone, Quarto, the R .deb and the Rust toolchain for
  Linux (``[ci.<platform>]``; CI and the container image only, not a supported desktop
  platform).
- ``cellar/``: the R packages that don't come from CRAN (opnca-r's vendored source
  tarball renamed to ``opnca_<ver>.tar.gz``, quartifyr, reportifyr, pyro).
- ``rrepo/``: a CRAN-like repository of the source tarball of every other package in
  ``runtime/r/renv.lock``, from ``runtime/r/binaries.toml``, with a ``PACKAGES`` index.
- ``wheelhouse/``: every wheel in the launcher's ``uv.lock`` (no dev groups) and in
  ``runtime/python/tools/uv.lock``, hash-checked, plus the launcher's own wheel.
- ``requirements/``: the hashed requirement lists exported from those locks.
- ``fetch-manifest.json``: every file with its SHA-256 and source URL.

opnca-r's repository is private. Either pass its vendored tarball with
``--opnca-vendored`` (downloaded elsewhere, e.g. with ``gh release download``), or use
``--opnca-release`` to download it here with ``GH_TOKEN`` (the container build reads the
token from a BuildKit secret mount). The token is sent only to api.github.com, never
printed, and dropped on the redirect to GitHub's storage. Without either, opnca-r is left
out and the offline phase skips the calculation step.

Wheels are chosen for ``--platform`` (default: this machine), not for the machine that
runs the fetch, so either Linux architecture can be fetched anywhere.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import os
import platform as host_platform
import re
import shutil
import subprocess
import sys
import tarfile
import time
import tomllib
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLATFORMS = ("linux-x86_64", "linux-aarch64")
# pip platform tags per platform. Ubuntu 24.04 has glibc 2.39; pip doesn't widen a
# manylinux tag to the older ones it implies, so list every one down to 2.17 (manylinux2014).
_ARCH = {"linux-x86_64": "x86_64", "linux-aarch64": "aarch64"}


def pip_platforms(platform: str) -> list[str]:
    arch = _ARCH[platform]
    tags = [f"manylinux_2_{minor}_{arch}" for minor in range(39, 16, -1)]
    tags.append(f"manylinux2014_{arch}")
    if arch == "x86_64":
        tags += ["manylinux_2_12_x86_64", "manylinux2010_x86_64", "manylinux_2_5_x86_64"]
        tags.append("manylinux1_x86_64")
    return tags


TOKEN_ENV = ("GH_TOKEN", "GITHUB_TOKEN")
_GITHUB_RELEASE = re.compile(
    r"^https://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)/releases/download/(?P<tag>[^/]+)/$"
)
CELLAR_PACKAGES = ("opnca-r", "quartifyr", "reportifyr", "pyro", "gitneighbr")
# DESCRIPTION fields that tools::write_PACKAGES puts in a repository index.
INDEX_FIELDS = (
    "Package",
    "Version",
    "Priority",
    "Depends",
    "Imports",
    "LinkingTo",
    "Suggests",
    "Enhances",
    "License",
    "License_is_FOSS",
    "License_restricts_use",
    "OS_type",
    "NeedsCompilation",
)


class FetchError(RuntimeError):
    pass


def host_linux_platform() -> str:
    machine = host_platform.machine().lower()
    return "linux-aarch64" if machine in ("aarch64", "arm64") else "linux-x86_64"


class _DropAuthOnRedirect(urllib.request.HTTPRedirectHandler):
    """GitHub answers an asset request with a redirect to its storage, which must not get
    the token (and refuses a request that has one)."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001, ANN201
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None:
            new.remove_header("Authorization")
        return new


def download_github_asset(release_url: str, name: str, dest: Path, sha256: str) -> Path:
    """Download one asset of a (private) GitHub release with ``GH_TOKEN``."""
    if dest.is_file() and sha256_file(dest) == sha256:
        return dest
    token = next((os.environ[k] for k in TOKEN_ENV if os.environ.get(k)), None)
    if not token:
        raise FetchError(f"{name} is a private release asset: set GH_TOKEN")
    m = _GITHUB_RELEASE.match(release_url)
    if m is None:
        raise FetchError(f"not a GitHub release URL: {release_url}")
    opener = urllib.request.build_opener(_DropAuthOnRedirect)
    auth = {"Authorization": f"Bearer {token}", "User-Agent": "opncafyr-fetch"}
    api = f"https://api.github.com/repos/{m['owner']}/{m['repo']}/releases/tags/{m['tag']}"
    req = urllib.request.Request(api, headers={**auth, "Accept": "application/vnd.github+json"})
    try:
        with opener.open(req, timeout=60) as resp:
            release = json.load(resp)
        asset_url = next(a["url"] for a in release["assets"] if a["name"] == name)
        if not asset_url.startswith("https://api.github.com/"):
            raise FetchError(f"unexpected asset URL for {name}")
        req = urllib.request.Request(  # noqa: S310 - https only, checked above
            asset_url, headers={**auth, "Accept": "application/octet-stream"}
        )
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_name(dest.name + ".part")
        with opener.open(req, timeout=600) as resp, part.open("wb") as f:
            shutil.copyfileobj(resp, f)
    except StopIteration:
        raise FetchError(f"{name} is not in the release") from None
    except OSError as exc:  # urllib errors, without the request headers
        raise FetchError(f"download of {name} failed: {type(exc).__name__}") from None
    if sha256_file(part) != sha256:
        part.unlink()
        raise FetchError(f"{name} does not match its pinned SHA-256")
    part.replace(dest)
    return dest


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download(url: str, dest: Path, sha256: str, *, attempts: int = 4) -> Path:
    """Download ``url`` to ``dest`` unless a file with the right hash is already there."""
    if dest.is_file() and sha256_file(dest) == sha256:
        return dest
    if not url.startswith("https://"):
        raise FetchError(f"{url}: only https URLs are fetched")
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    for attempt in range(1, attempts + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "opncafyr-offline-ci"})  # noqa: S310 - https only, checked above
            with urllib.request.urlopen(req, timeout=120) as resp, tmp.open("wb") as out:  # noqa: S310
                shutil.copyfileobj(resp, out, 1 << 20)
            break
        except OSError as exc:
            if attempt == attempts:
                raise FetchError(f"{url}: {exc}") from exc
            time.sleep(2**attempt)
    got = sha256_file(tmp)
    if got != sha256:
        tmp.unlink()
        raise FetchError(f"{url}: SHA-256 {got} does not match the pin {sha256}")
    tmp.replace(dest)
    return dest


class Fetcher:
    def __init__(self, pins_path: Path, out: Path, platform: str) -> None:
        self.pins = tomllib.loads(pins_path.read_text("utf-8"))
        self.out = out
        self.platform = platform
        self.records: list[dict[str, str]] = []

    def _get(self, url: str, dest: Path, sha256: str) -> Path:
        path = download(url, dest, sha256)
        self.records.append({"path": str(path.relative_to(self.out)), "sha256": sha256, "url": url})
        print(f"ok  {path.relative_to(self.out)}", file=sys.stderr)
        return path

    def runtimes(self) -> dict[str, str]:
        try:
            section = self.pins["ci"][self.platform]
        except KeyError:
            raise FetchError(f"runtime/pins.toml has no [ci.{self.platform}] section") from None
        names = {}
        for name, entry in section.items():
            asset = entry["assets"][self.platform]
            url, sha = asset["url"], asset["sha256"]
            filename = url.rsplit("/", 1)[1].replace("%2B", "+")
            self._get(url, self.out / "runtime" / filename, sha)
            names[name] = filename
        return names

    def libreoffice(self) -> str:
        """The -full image's LibreOffice .deb set ([container.libreoffice], #42)."""
        asset = self.pins["container"]["libreoffice"]["assets"][self.platform]
        url, sha = asset["url"], asset["sha256"]
        filename = url.rsplit("/", 1)[1]
        self._get(url, self.out / "runtime" / filename, sha)
        return filename

    def cellar(self, opnca_vendored: Path | None, opnca_release: bool = False) -> list[str]:
        packages = self.pins["packages"]
        kept = []
        for key in CELLAR_PACKAGES:
            pin = packages[key]
            version = pin["version"]
            r_name = "opnca" if key == "opnca-r" else key
            dest = self.out / "cellar" / f"{r_name}_{version}.tar.gz"
            if key == "opnca-r":
                vendored = f"opnca_{version}-vendored.tar.gz"
                sha = pin["assets"][vendored]
                if opnca_release:
                    download_github_asset(pin["release_url"], vendored, dest, sha)
                    self.records.append(
                        {"path": str(dest.relative_to(self.out)), "sha256": sha, "url": vendored}
                    )
                    print(f"ok  {dest.relative_to(self.out)}", file=sys.stderr)
                    kept.append(r_name)
                    continue
                if opnca_vendored is None:
                    print("skip opnca-r (no --opnca-vendored or --opnca-release)", file=sys.stderr)
                    continue
                if sha256_file(opnca_vendored) != sha:
                    raise FetchError(f"{opnca_vendored} does not match the pin for {vendored}")
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(opnca_vendored, dest)
                self.records.append(
                    {"path": str(dest.relative_to(self.out)), "sha256": sha, "url": vendored}
                )
            else:
                asset = f"{r_name}_{version}.tar.gz"
                self._get(pin["release_url"] + asset, dest, pin["assets"][asset])
            kept.append(r_name)
        return kept

    def r_repository(self) -> int:
        lock = json.loads((ROOT / self.pins["r_packages"]["lockfile"]).read_text("utf-8"))
        binaries = tomllib.loads((ROOT / self.pins["r_packages"]["binaries"]).read_text("utf-8"))
        contrib = self.out / "rrepo" / "src" / "contrib"
        count = 0
        for name, record in sorted(lock["Packages"].items()):
            if record.get("Source") != "Repository":
                continue
            source = binaries["packages"][name]["source"]
            if source["version"] != record["Version"]:
                raise FetchError(
                    f"{name}: binaries.toml has {source['version']}, renv.lock {record['Version']}"
                )
            dest = contrib / f"{name}_{record['Version']}.tar.gz"
            self._get(source["url"], dest, source["sha256"])
            count += 1
        write_packages_index(contrib)
        return count

    def wheelhouse(self, uv: str) -> None:
        reqs = self.out / "requirements"
        reqs.mkdir(parents=True, exist_ok=True)
        wheels = self.out / "wheelhouse"
        wheels.mkdir(parents=True, exist_ok=True)
        exports = {
            "launcher": [uv, "export", "--frozen", "--no-dev", "--no-emit-project"],
            "tools": [
                uv,
                "export",
                "--frozen",
                "--no-emit-project",
                "--project",
                str(ROOT / "runtime" / "python" / "tools"),
            ],
        }
        for name, argv in exports.items():
            text = _run([*argv, "--format", "requirements-txt", "--quiet"], cwd=ROOT)
            (reqs / f"{name}.txt").write_text(text, "utf-8")
            # pip download fetches the direct-URL wheel (deckifyr) once, here. The offline
            # install must take it from the wheelhouse, so that copy names it by version.
            (reqs / f"{name}.offline.txt").write_text(pin_direct_urls(text), "utf-8")
            _run(
                [
                    uv,
                    "run",
                    "--no-project",
                    "--python",
                    self.pins["python"]["version"],
                    "--with",
                    "pip==25.3",
                    "python",
                    "-m",
                    "pip",
                    "download",
                    "--quiet",
                    "--no-deps",
                    "--only-binary=:all:",
                    *(arg for tag in pip_platforms(self.platform) for arg in ("--platform", tag)),
                    "--python-version",
                    self.pins["python"]["version"].rsplit(".", 1)[0],
                    "--implementation",
                    "cp",
                    "--require-hashes",
                    "--dest",
                    str(wheels),
                    "--requirement",
                    str(reqs / f"{name}.txt"),
                ],
                cwd=ROOT,
            )
        _run([uv, "build", "--wheel", "--quiet", "--out-dir", str(wheels)], cwd=ROOT)
        for wheel in sorted(wheels.glob("*.whl")):
            self.records.append(
                {
                    "path": str(wheel.relative_to(self.out)),
                    "sha256": sha256_file(wheel),
                    "url": "uv.lock",
                }
            )

    def write_manifest(self, extra: dict[str, object]) -> Path:
        path = self.out / "fetch-manifest.json"
        doc = {"schema_version": 1, "platform": self.platform, **extra, "files": self.records}
        path.write_text(json.dumps(doc, indent=2) + "\n", "utf-8")
        return path


def _run(argv: list[str], cwd: Path) -> str:
    print("+", " ".join(argv), file=sys.stderr)
    done = subprocess.run(argv, cwd=cwd, check=True, capture_output=True, text=True)  # noqa: S603 - CI tooling, argv only
    if done.stderr:
        sys.stderr.write(done.stderr)
    return done.stdout


_DIRECT_URL = re.compile(r"^(?P<name>[A-Za-z0-9._-]+)(?:\[[^\]]*\])? @ \S+/(?P<file>[^/\s]+\.whl)")


def pin_direct_urls(text: str) -> str:
    """Turn ``name @ https://…/name-1.0-py3-none-any.whl`` into ``name==1.0``.

    The hash lines that follow are kept, so the wheel is still checked byte for byte.
    """
    out = []
    for line in text.splitlines(keepends=True):
        m = _DIRECT_URL.match(line)
        if m:
            version = m.group("file").split("-")[1]
            rest = line[m.end() :]
            line = f"{m.group('name')}=={version}{rest}"
        out.append(line)
    return "".join(out)


def read_description(tarball: Path) -> dict[str, str]:
    package = tarball.name.split("_", 1)[0]
    with tarfile.open(tarball, "r:gz") as tar:
        member = tar.extractfile(f"{package}/DESCRIPTION")
        if member is None:
            raise FetchError(f"{tarball.name} has no {package}/DESCRIPTION")
        text = member.read().decode("utf-8", errors="replace")
    fields: dict[str, str] = {}
    last = None
    for line in text.splitlines():
        if line[:1] in (" ", "\t") and last:
            fields[last] += " " + line.strip()
        elif ":" in line:
            last, value = line.split(":", 1)
            fields[last] = value.strip()
    return fields


def write_packages_index(contrib: Path) -> None:
    """Write PACKAGES and PACKAGES.gz like ``tools::write_PACKAGES(type = "source")``."""
    blocks = []
    for tarball in sorted(contrib.glob("*.tar.gz")):
        desc = read_description(tarball)
        lines = [f"{k}: {desc[k]}" for k in INDEX_FIELDS if k in desc]
        lines.append(f"MD5sum: {hashlib.md5(tarball.read_bytes()).hexdigest()}")  # noqa: S324 - index format
        blocks.append("\n".join(lines))
    text = "\n\n".join(blocks) + "\n"
    (contrib / "PACKAGES").write_text(text, "utf-8")
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0) as gz:
        gz.write(text.encode("utf-8"))
    (contrib / "PACKAGES.gz").write_bytes(buf.getvalue())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--pins", type=Path, default=ROOT / "runtime" / "pins.toml")
    parser.add_argument("--platform", choices=PLATFORMS, default=host_linux_platform())
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--opnca-vendored", type=Path)
    source.add_argument("--opnca-release", action="store_true", help="download it with GH_TOKEN")
    parser.add_argument(
        "--with-libreoffice", action="store_true", help="also LibreOffice, for the -full image"
    )
    parser.add_argument("--uv", default=shutil.which("uv") or "uv")
    args = parser.parse_args(argv)
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    fetcher = Fetcher(args.pins, out, args.platform)
    try:
        runtimes = fetcher.runtimes()
        if args.with_libreoffice:
            runtimes["libreoffice"] = fetcher.libreoffice()
        cellar = fetcher.cellar(args.opnca_vendored, args.opnca_release)
        count = fetcher.r_repository()
        fetcher.wheelhouse(args.uv)
    except (FetchError, subprocess.CalledProcessError, KeyError) as exc:
        print(f"fetch failed: {exc}", file=sys.stderr)
        return 1
    manifest = fetcher.write_manifest(
        {"runtimes": runtimes, "cellar": cellar, "r_repository_packages": count}
    )
    print(manifest)
    return 0


if __name__ == "__main__":
    sys.exit(main())
