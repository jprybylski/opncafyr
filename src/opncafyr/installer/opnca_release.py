"""Select and verify opnca-r release assets (ADR 0004). Makes no network calls.

The installer (#19, #34), the offline bundle builder (#36) and the CI proxy harness (#16)
all use this one code path. Given a downloaded ``manifest.json`` and our
``runtime/pins.toml``, it:

1. checks the manifest's SHA-256 against the pin, and its package, version and source
   commit;
2. picks the asset by ``kind``, ``platform``, ``arch`` and ``r_minor``, never by file
   name;
3. checks the downloaded asset against both the manifest and the pinned SHA-256.

Usage::

    python -m opncafyr.installer.opnca_release select --pins runtime/pins.toml \\
        --manifest dl/manifest.json            # prints the asset file name to fetch
    python -m opncafyr.installer.opnca_release verify --pins runtime/pins.toml \\
        --manifest dl/manifest.json --asset dl/<name>   # prints the install file name
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform as _platform
import sys
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

PIN_KEY = "opnca-r"


class ReleaseError(ValueError):
    """The release does not match what we pinned."""


def load_pins(path: str | Path) -> dict[str, Any]:
    with open(path, "rb") as fh:
        pins = tomllib.load(fh)
    try:
        return pins["packages"][PIN_KEY]
    except KeyError:
        raise ReleaseError(f"No [packages.{PIN_KEY}] in {path}.") from None


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def host_platform() -> tuple[str, str]:
    """Our (platform, arch) names for this machine, as opnca-r's manifest spells them."""
    plat = {"darwin": "macos", "win32": "windows", "linux": "linux"}.get(sys.platform)
    machine = _platform.machine().lower()
    arch = {"arm64": "arm64", "aarch64": "arm64", "x86_64": "x86_64", "amd64": "x86_64"}.get(
        machine
    )
    if plat is None or arch is None:
        raise ReleaseError(f"Unsupported platform {sys.platform}/{machine}.")
    return plat, arch


def verify_manifest(manifest_path: str | Path, pins: Mapping[str, Any]) -> dict[str, Any]:
    actual = sha256_file(manifest_path)
    if actual != pins.get("manifest_sha256"):
        raise ReleaseError("manifest.json does not match the pinned SHA-256.")
    manifest = json.loads(Path(manifest_path).read_text("utf-8"))
    expected = {
        "schema_version": 1,
        "package": "opnca",
        "version": pins.get("version"),
        "source_commit": pins.get("source_commit"),
    }
    for key, value in expected.items():
        if manifest.get(key) != value:
            raise ReleaseError(f"manifest.json {key} is {manifest.get(key)!r}, not {value!r}.")
    return manifest


def select_asset(
    manifest: Mapping[str, Any],
    *,
    kind: str = "binary",
    platform: str | None = None,
    arch: str | None = None,
    r_minor: str | None = None,
) -> dict[str, Any]:
    """The one manifest entry matching these fields. Source assets ignore platform/arch."""
    if kind == "binary":
        if platform is None or arch is None:
            platform, arch = host_platform()
        wanted = {"kind": kind, "platform": platform, "arch": arch, "r_minor": r_minor}
    else:
        wanted = {"kind": kind}
    matches = [
        a
        for a in manifest.get("assets", [])
        if all(v is None or a.get(k) == v for k, v in wanted.items())
    ]
    if len(matches) != 1:
        shown = {k: v for k, v in wanted.items() if v is not None}
        raise ReleaseError(f"Expected one asset for {shown}, found {len(matches)}.")
    return dict(matches[0])


def verify_asset(path: str | Path, entry: Mapping[str, Any], pins: Mapping[str, Any]) -> None:
    name = entry["asset"]
    pinned = pins.get("assets", {}).get(name)
    if pinned is None:
        raise ReleaseError(f"{name} is not pinned in runtime/pins.toml.")
    if pinned != entry.get("sha256"):
        raise ReleaseError(f"The manifest and the pin disagree about {name}.")
    file = Path(path)
    if "bytes" in entry and file.stat().st_size != entry["bytes"]:
        raise ReleaseError(f"{name} has the wrong size.")
    if sha256_file(file) != pinned:
        raise ReleaseError(f"{name} does not match the pinned SHA-256.")


def install_name(entry: Mapping[str, Any], version: str) -> str:
    """The file name R CMD INSTALL and the renv cellar expect (opnca_<ver>.<ext>)."""
    name = entry["asset"]
    for ext in (".tar.gz", ".tgz", ".zip"):
        if name.endswith(ext):
            return f"opnca_{version}{ext}"
    raise ReleaseError(f"Unknown archive type: {name}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m opncafyr.installer.opnca_release")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("select", "verify"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--pins", required=True)
        cmd.add_argument("--manifest", required=True)
        cmd.add_argument("--kind", default="binary")
        cmd.add_argument("--platform")
        cmd.add_argument("--arch")
        if name == "verify":
            cmd.add_argument("--asset", required=True)
    args = parser.parse_args(argv)
    try:
        pins = load_pins(args.pins)
        manifest = verify_manifest(args.manifest, pins)
        entry = select_asset(
            manifest,
            kind=args.kind,
            platform=args.platform,
            arch=args.arch,
            r_minor=pins.get("r_minor") if args.kind == "binary" else None,
        )
        if args.command == "select":
            print(entry["asset"])
        else:
            verify_asset(args.asset, entry, pins)
            print(install_name(entry, pins["version"]))
    except (ReleaseError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
