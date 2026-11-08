"""Online phase of the offline CI job (#44): fetch every pinned asset into one folder.

    python scripts/offline/fetch.py --out <assets> [--opnca-vendored <file>]

Everything comes from ``runtime/pins.toml`` and the files it points to, and every file is
checked against its pinned SHA-256 before it is kept. The offline phase
(``offline_phase.sh``) then installs and runs from ``<assets>`` with the network cut.

Layout of ``<assets>``:

- ``runtime/``: uv, python-build-standalone, Quarto, the R .deb and the Rust toolchain for
  Linux (``[ci.linux-x86_64]``; CI and container only, not a supported desktop platform).
- ``cellar/``: the R packages that don't come from CRAN (opnca-r's vendored source
  tarball renamed to ``opnca_<ver>.tar.gz``, quartifyr, reportifyr, pyro).
- ``rrepo/``: a CRAN-like repository of the source tarball of every other package in
  ``runtime/r/renv.lock``, from ``runtime/r/binaries.toml``, with a ``PACKAGES`` index.
- ``wheelhouse/``: every wheel in the launcher's ``uv.lock`` (no dev groups) and in
  ``runtime/python/tools/uv.lock``, hash-checked, plus the launcher's own wheel.
- ``requirements/``: the hashed requirement lists exported from those locks.
- ``fetch-manifest.json``: every file with its SHA-256 and source URL.

opnca-r's repository is private, so the workflow downloads its vendored tarball with a
token and passes it in with ``--opnca-vendored``; without it, opnca-r is left out and the
offline phase skips the calculation step.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
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
PLATFORM = "linux-x86_64"
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
    def __init__(self, pins_path: Path, out: Path) -> None:
        self.pins = tomllib.loads(pins_path.read_text("utf-8"))
        self.out = out
        self.records: list[dict[str, str]] = []

    def _get(self, url: str, dest: Path, sha256: str) -> Path:
        path = download(url, dest, sha256)
        self.records.append({"path": str(path.relative_to(self.out)), "sha256": sha256, "url": url})
        print(f"ok  {path.relative_to(self.out)}", file=sys.stderr)
        return path

    def runtimes(self) -> dict[str, str]:
        try:
            section = self.pins["ci"][PLATFORM]
        except KeyError:
            raise FetchError(f"runtime/pins.toml has no [ci.{PLATFORM}] section") from None
        names = {}
        for name, entry in section.items():
            asset = entry["assets"][PLATFORM]
            url, sha = asset["url"], asset["sha256"]
            filename = url.rsplit("/", 1)[1].replace("%2B", "+")
            self._get(url, self.out / "runtime" / filename, sha)
            names[name] = filename
        return names

    def cellar(self, opnca_vendored: Path | None) -> list[str]:
        packages = self.pins["packages"]
        kept = []
        for key in CELLAR_PACKAGES:
            pin = packages[key]
            version = pin["version"]
            r_name = "opnca" if key == "opnca-r" else key
            dest = self.out / "cellar" / f"{r_name}_{version}.tar.gz"
            if key == "opnca-r":
                vendored = f"opnca_{version}-vendored.tar.gz"
                if opnca_vendored is None:
                    print("skip opnca-r (no --opnca-vendored)", file=sys.stderr)
                    continue
                sha = pin["assets"][vendored]
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
        doc = {"schema_version": 1, "platform": PLATFORM, **extra, "files": self.records}
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
    parser.add_argument("--opnca-vendored", type=Path)
    parser.add_argument("--uv", default=shutil.which("uv") or "uv")
    args = parser.parse_args(argv)
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    fetcher = Fetcher(args.pins, out)
    try:
        runtimes = fetcher.runtimes()
        cellar = fetcher.cellar(args.opnca_vendored)
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
