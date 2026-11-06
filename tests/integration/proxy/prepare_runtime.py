"""Build a runtime record for the proxy harness from verified release assets.

Development and CI only; the installer (#19) does the real install. Downloads are a
separate step (see docs/spikes/basepath.md), so this script never touches the network
except for ``uv pip install`` of the deckifyr wheel's dependencies.

    uv run python tests/integration/proxy/prepare_runtime.py --out <dir> \\
        [--opnca-dir <dir with manifest.json and the selected binary>] \\
        [--deckifyr-wheel <path>]

It prints the path of the ``runtime.json`` it writes. Point ``OPNCAFYR_RUNTIME_FILE``
at it.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

from opncafyr.adapters.opnca import release

ROOT = Path(__file__).resolve().parents[3]
PINS = ROOT / "runtime" / "pins.toml"


def _run(argv: list[str]) -> None:
    print("+", " ".join(argv), file=sys.stderr)
    subprocess.run(argv, check=True)  # noqa: S603 - dev/CI tooling, argv only


def install_opnca(opnca_dir: Path, out: Path, rscript: Path) -> Path:
    pins = release.load_pins(PINS)
    manifest = release.verify_manifest(opnca_dir / "manifest.json", pins)
    entry = release.select_asset(manifest, r_minor=pins["r_minor"])
    asset = opnca_dir / entry["asset"]
    release.verify_asset(asset, entry, pins)
    stage = out / "stage"
    stage.mkdir(parents=True, exist_ok=True)
    staged = stage / release.install_name(entry, pins["version"])
    shutil.copyfile(asset, staged)
    lib = out / "rlib"
    lib.mkdir(exist_ok=True)
    r = rscript.with_name("R.exe" if os.name == "nt" else "R")
    _run([str(r), "CMD", "INSTALL", "--no-test-load", f"--library={lib}", str(staged)])
    if not (lib / "opnca" / "scripts" / "serve.R").is_file():
        raise SystemExit("opnca installed without scripts/serve.R")
    return lib


def install_deckifyr(wheel: Path, out: Path, python_version: str) -> Path:
    pins = tomllib.loads(PINS.read_text("utf-8"))["packages"]["deckifyr"]
    if wheel.name != pins["wheel"]:
        raise SystemExit(f"Expected {pins['wheel']}, got {wheel.name}")
    if release.sha256_file(wheel) != pins["wheel_sha256"]:
        raise SystemExit(f"{wheel.name} does not match the pinned SHA-256")
    venv = out / "deckifyr-venv"
    uv = shutil.which("uv")
    if uv is None:
        raise SystemExit("uv is required")
    _run([uv, "venv", "--quiet", "--python", python_version, str(venv)])
    python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    _run([uv, "pip", "install", "--quiet", "--python", str(python), f"{wheel}[web]"])
    return python


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--opnca-dir", type=Path)
    parser.add_argument("--deckifyr-wheel", type=Path)
    parser.add_argument("--rscript", type=Path, default=shutil.which("Rscript"))
    parser.add_argument("--python", default="3.13")
    args = parser.parse_args()

    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    doc: dict[str, object] = {"schema_version": 1, "executables": {}}
    executables: dict[str, str] = doc["executables"]  # type: ignore[assignment]
    if args.opnca_dir:
        if args.rscript is None:
            raise SystemExit("Rscript not found; pass --rscript")
        rscript = Path(args.rscript).resolve()
        doc["r_library"] = str(install_opnca(args.opnca_dir.resolve(), out, rscript))
        executables["rscript"] = str(rscript)
    if args.deckifyr_wheel:
        python = install_deckifyr(args.deckifyr_wheel.resolve(), out, args.python)
        executables["deckifyr_python"] = str(python)
    runtime = out / "runtime.json"
    runtime.write_text(json.dumps(doc, indent=2) + "\n")
    print(runtime)
    return 0


if __name__ == "__main__":
    sys.exit(main())
