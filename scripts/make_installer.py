"""Build the installer payload (#19): the folder (and zip) a user downloads and runs.

    uv run python scripts/make_installer.py                 # dist/opncafyr-installer-<ver>/
    uv run python scripts/make_installer.py --zip           # also dist/opncafyr-installer-<ver>.zip
    uv run python scripts/make_installer.py --version 0.0.2 # CI: a payload for a newer version

Contents::

    install.sh  install.bat  install.ps1  run_installer.py
    bootstrap.tsv          Python archive per platform (file, SHA-256, URL) from the pins
    opncafyr-<ver>-py3-none-any.whl
    runtime/pins.toml  runtime/r/renv.lock  runtime/r/binaries.toml  runtime/python/wheels.toml
    payload.json           every other file with its SHA-256, and their digest (payload-v1)

``--version`` builds the wheel from a temporary copy of the repository with ``VERSION`` and
``pyproject.toml`` changed, to test updates (#36); release payloads never use it.
Dev and CI tooling: building the wheel needs hatchling from PyPI.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WRAPPERS = ("install.sh", "install.bat", "install.ps1", "run_installer.py")
RUNTIME = (
    "runtime/pins.toml",
    "runtime/r/renv.lock",
    "runtime/r/binaries.toml",
    "runtime/python/wheels.toml",
)
ZIP_TIME = (2020, 2, 2, 0, 0, 0)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def bootstrap_tsv(pins: dict) -> str:
    rows = []
    for platform in pins["platforms"]["supported"]:
        entry = pins["python"]["assets"][platform]
        url = entry["url"]
        name = url.rsplit("/", 1)[1].replace("%2B", "+")
        rows.append("\t".join([platform, name, entry["sha256"], url]))
    return "\n".join(rows) + "\n"


def digest_of(files: dict[str, str]) -> str:
    return hashlib.sha256(
        json.dumps(files, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _check_wheel():
    """``scripts/check_wheel.check_wheel``, loaded by path (tests import this file by path)."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "check_wheel", Path(__file__).with_name("check_wheel.py")
    )
    assert spec is not None and spec.loader is not None  # noqa: S101
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.check_wheel


def build_wheel(out: Path, version: str | None) -> Path:
    uv = shutil.which("uv")
    if uv is None:
        raise SystemExit("uv is required on PATH")
    src = ROOT
    tmp = None
    if version is not None:
        tmp = Path(tempfile.mkdtemp(prefix="opncafyr-src-"))
        src = tmp / "repo"
        shutil.copytree(
            ROOT,
            src,
            ignore=shutil.ignore_patterns(".git", ".venv", "dist", "__pycache__", "*.pyc"),
        )
        (src / "VERSION").write_text(version + "\n", "utf-8")
        pyproject = src / "pyproject.toml"
        text = re.sub(
            r'(?m)^version = ".*"$', f'version = "{version}"', pyproject.read_text("utf-8"), count=1
        )
        pyproject.write_text(text, "utf-8")
    try:
        before = set(out.glob("*.whl"))
        subprocess.run(  # noqa: S603 - dev tool, fixed argv
            [uv, "build", "--wheel", "--quiet", "--out-dir", str(out), str(src)],
            check=True,
        )
        built = sorted(set(out.glob("*.whl")) - before) or sorted(out.glob("opncafyr-*.whl"))
        problems = _check_wheel()(built[-1])
        if problems:
            raise SystemExit(f"{built[-1].name}: " + "; ".join(problems))
        return built[-1]
    finally:
        if tmp is not None:
            shutil.rmtree(tmp, ignore_errors=True)


def make(out_root: Path, *, version: str | None = None, make_zip: bool = False) -> Path:
    pins = tomllib.loads((ROOT / "runtime/pins.toml").read_text("utf-8"))
    ver = version or (ROOT / "VERSION").read_text("utf-8").strip()
    dest = out_root / f"opncafyr-installer-{ver}"
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    with tempfile.TemporaryDirectory() as tmp:
        wheel = build_wheel(Path(tmp), version)
        if not wheel.name.startswith(f"opncafyr-{ver}-"):
            raise SystemExit(f"built {wheel.name}, expected version {ver}")
        shutil.copy2(wheel, dest / wheel.name)
    for name in WRAPPERS:
        shutil.copy2(ROOT / "installer" / name, dest / name)
    (dest / "install.sh").chmod(0o755)
    (dest / "bootstrap.tsv").write_text(bootstrap_tsv(pins), "utf-8")
    for rel in RUNTIME:
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / rel, target)
    files = {
        p.relative_to(dest).as_posix(): sha256(p)
        for p in sorted(dest.rglob("*"))
        if p.is_file() and p.name != "payload.json"
    }
    doc = {"schema_version": 1, "version": ver, "files": files, "digest": digest_of(files)}
    (dest / "payload.json").write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", "utf-8")
    if make_zip:
        archive = out_root / f"{dest.name}.zip"
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
            for path in sorted(dest.rglob("*")):
                if path.is_file():
                    info = zipfile.ZipInfo(f"{dest.name}/{path.relative_to(dest).as_posix()}")
                    info.date_time = ZIP_TIME
                    info.external_attr = (0o755 if path.name == "install.sh" else 0o644) << 16
                    info.compress_type = zipfile.ZIP_DEFLATED
                    zf.writestr(info, path.read_bytes())
        print(archive)
    print(dest)
    return dest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=ROOT / "dist")
    parser.add_argument("--version", help="build for this version instead of VERSION (tests)")
    parser.add_argument("--zip", action="store_true", help="also write a zip of the payload")
    args = parser.parse_args(argv)
    make(args.out.resolve(), version=args.version, make_zip=args.zip)
    return 0


if __name__ == "__main__":
    sys.exit(main())
