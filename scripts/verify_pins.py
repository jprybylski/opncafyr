"""Check runtime/pins.toml and the locks it points to (#5).

    uv run python scripts/verify_pins.py                 # structure only, no network
    uv run python scripts/verify_pins.py --network       # also download and hash every URL
    uv run python scripts/verify_pins.py --network --only uv,quarto

Without ``--network`` it checks that:
- every runtime asset (``[<name>.assets.<platform>]``) has an ``https`` URL and a
  64-hex SHA-256 for each platform it covers (all of ``[platforms].supported`` unless it
  lists ``platforms``);
- every release package has a ``release_url`` and a SHA-256 for each asset, and opnca-r
  has a binary for each supported platform;
- no value is ``TBD`` and no URL names a moving ref (``main``, ``master``, ``latest``,
  ``HEAD``, a bare r-universe repository);
- ``runtime/r/renv.lock`` and ``runtime/r/binaries.toml`` agree: every repository package
  in the lock has a binary per supported platform plus a source tarball, at the locked
  version, from the lock's snapshot; every cellar package in the lock is pinned here;
- each Python environment's ``uv.lock`` exists and pins the deckifyr wheel with the same
  SHA-256 as here;
- every runtime section and release package names its ``license`` (SPDX expression), for
  the offline bundle's licence inventory (#36);
- ``runtime/python/wheels.toml`` has a wheel list per environment and platform, each with
  an https URL and a SHA-256, and the tools list carries the pinned deckifyr wheel. Run
  as a script, it also checks that the file matches the locks (``pin_wheels.py --check``).

With ``--network`` it downloads each URL (streaming) and compares the SHA-256. Assets on
private GitHub repositories are fetched through the API with ``GH_TOKEN`` or
``GITHUB_TOKEN``; without a token they are reported as skipped, not passed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
MOVING = re.compile(
    r"/(main|master|HEAD|latest)(/|$)|releases/latest|r-universe\.dev(?!/.+/[0-9a-f]{40})",
    re.IGNORECASE,
)
# Packages pinned from CRAN's own macOS build instead of the snapshot (binaries.toml
# `origin = "cran"`; see scripts/pin_r_binaries.py).
CRAN_BIN = "https://cran.r-project.org/bin/macosx/"
CELLAR = ("opnca-r", "quartifyr", "reportifyr", "pyro")
CELLAR_R_NAMES = {"opnca-r": "opnca"}
# Release packages a platform binary must exist for, and how to spot it in a file name.
PLATFORM_BINARIES = {"opnca-r": "_{platform}."}


@dataclass(frozen=True)
class Asset:
    section: str
    label: str
    url: str
    sha256: str
    private: bool = False


def load(path: Path) -> dict[str, Any]:
    return tomllib.loads(path.read_text("utf-8"))


def _walk_strings(node: Any, where: str = "") -> Iterator[tuple[str, str]]:
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _walk_strings(value, f"{where}.{key}" if where else key)
    elif isinstance(node, list):
        for i, value in enumerate(node):
            yield from _walk_strings(value, f"{where}[{i}]")
    elif isinstance(node, str):
        yield where, node


def _runtime_sections(pins: dict[str, Any]) -> Iterator[tuple[str, dict[str, Any]]]:
    """Every table that has an ``assets`` table keyed by platform."""

    def walk(node: dict[str, Any], where: str) -> Iterator[tuple[str, dict[str, Any]]]:
        for key, value in node.items():
            if not isinstance(value, dict):
                continue
            name = f"{where}.{key}" if where else key
            if where.startswith("packages") or name.startswith("packages"):
                continue
            assets = value.get("assets")
            if isinstance(assets, dict) and all(isinstance(v, dict) for v in assets.values()):
                yield name, value
            yield from walk(value, name)

    yield from walk(pins, "")


def check_structure(pins: dict[str, Any], root: Path = ROOT) -> tuple[list[str], list[Asset]]:
    errors: list[str] = []
    assets: list[Asset] = []
    supported = pins.get("platforms", {}).get("supported", [])
    if not supported:
        errors.append("[platforms].supported is empty")

    for where, value in _walk_strings(pins):
        if value.strip().upper().startswith("TBD"):
            errors.append(f"{where} is still TBD")
        if value.startswith(("http://", "https://")) and MOVING.search(value):
            errors.append(f"{where} names a moving ref: {value}")
        if value.startswith("http://"):
            errors.append(f"{where} is not https: {value}")

    # Runtime assets, per platform.
    for name, section in _runtime_sections(pins):
        if section.get("status") == "unavailable":
            continue
        wanted = section.get("platforms", supported)
        for platform in wanted:
            entry = section["assets"].get(platform)
            if entry is None:
                errors.append(f"[{name}] has no asset for {platform}")
                continue
            url, sha = entry.get("url", ""), entry.get("sha256", "")
            if not url.startswith("https://"):
                errors.append(f"[{name}.assets.{platform}] url missing or not https")
            if not SHA_RE.match(sha):
                errors.append(f"[{name}.assets.{platform}] sha256 missing or malformed")
            assets.append(Asset(name, platform, url, sha))
        if not str(section.get("license", "")).strip():
            errors.append(f"[{name}] has no license")

    # Release packages: release_url + filename -> sha256.
    for pkg, section in pins.get("packages", {}).items():
        files = section.get("assets")
        base = section.get("release_url", "")
        if not files:
            errors.append(f"[packages.{pkg}] has no assets")
            continue
        if not str(section.get("license", "")).strip():
            errors.append(f"[packages.{pkg}] has no license")
        if not base.startswith("https://") or not base.endswith("/"):
            errors.append(f"[packages.{pkg}] release_url missing or does not end with /")
        for filename, sha in files.items():
            if not SHA_RE.match(str(sha)):
                errors.append(f"[packages.{pkg}.assets] {filename} sha256 malformed")
            private = section.get("private", False)
            assets.append(Asset(f"packages.{pkg}", filename, base + filename, sha, private))
        pattern = PLATFORM_BINARIES.get(pkg)
        if pattern:
            for platform in supported:
                if not any(pattern.format(platform=platform) in f for f in files):
                    errors.append(f"[packages.{pkg}] has no binary for {platform}")
    for pkg in CELLAR:
        if pkg not in pins.get("packages", {}):
            errors.append(f"[packages.{pkg}] is missing")
    errors += _check_source_build(pins, supported)

    errors += _check_r_packages(pins, supported, root, assets)
    errors += _check_python_envs(pins, root)
    errors += _check_wheels(pins, supported, root, assets)
    return errors, assets


def _check_source_build(pins: dict[str, Any], supported: list[str]) -> list[str]:
    """What ``repair --build-from-source`` needs (#34) is pinned for every platform."""
    errors = []
    opnca = pins.get("packages", {}).get("opnca-r", {})
    files = opnca.get("assets", {})
    if opnca.get("manifest_sha256") != files.get("manifest.json"):
        errors.append("[packages.opnca-r] manifest_sha256 differs from assets.manifest.json")
    vendored = [f for f in files if f.endswith("-vendored.tar.gz")]
    if len(vendored) != 1:
        errors.append(f"[packages.opnca-r] needs one -vendored.tar.gz, has {len(vendored)}")
    rust = pins.get("rust", {})
    if not rust.get("toolchain"):
        errors.append("[rust] has no toolchain version")
    standalone = rust.get("standalone", {}).get("assets", {})
    for platform in supported:
        if platform not in standalone:
            errors.append(f"[rust.standalone] has no archive for {platform}")
    rtools = pins.get("rtools", {})
    for platform in supported:
        if platform.startswith("windows") and platform not in rtools.get("assets", {}):
            errors.append(f"[rtools] has no installer for {platform}")
    return errors


WHEELS = "runtime/python/wheels.toml"


def _check_wheels(
    pins: dict[str, Any], supported: list[str], root: Path, assets: list[Asset]
) -> list[str]:
    path = root / WHEELS
    if not path.is_file():
        return [f"{WHEELS} does not exist (run scripts/pin_wheels.py)"]
    errors: list[str] = []
    wheels = load(path)
    for env in pins.get("python_envs", {}):
        for platform in supported:
            entries = wheels.get(env, {}).get(platform, [])
            if not entries:
                errors.append(f"{WHEELS} has no [{env}.{platform}] wheels")
            for w in entries:
                if not w.get("url", "").startswith("https://") or not SHA_RE.match(
                    w.get("sha256", "")
                ):
                    errors.append(f"{WHEELS} {env} {platform} {w.get('file')}: bad url or sha256")
                assets.append(
                    Asset(f"wheels.{env}", f"{platform} {w.get('file')}", w["url"], w["sha256"])
                )
    deck = pins.get("packages", {}).get("deckifyr", {})
    want = deck.get("assets", {}).get(deck.get("wheel", ""))
    for platform in supported:
        got = [
            w["sha256"]
            for w in wheels.get("tools", {}).get(platform, [])
            if w["name"] == "deckifyr"
        ]
        if got != [want]:
            errors.append(f"{WHEELS} [tools.{platform}] does not pin the deckifyr wheel")
    return errors


def _check_r_packages(
    pins: dict[str, Any], supported: list[str], root: Path, assets: list[Asset]
) -> list[str]:
    errors: list[str] = []
    section = pins.get("r_packages")
    if not section:
        return ["[r_packages] is missing"]
    lock_path, bin_path = root / section["lockfile"], root / section["binaries"]
    if not lock_path.is_file():
        return [f"{section['lockfile']} does not exist"]
    if not bin_path.is_file():
        return [f"{section['binaries']} does not exist"]
    lock = json.loads(lock_path.read_text("utf-8"))
    binaries = load(bin_path)
    r_version = pins.get("r", {}).get("version")
    if lock["R"]["Version"] != r_version:
        errors.append(f"renv.lock is for R {lock['R']['Version']}, pins say {r_version}")
    repo = next((r["URL"] for r in lock["R"]["Repositories"] if r["Name"] == "P3M"), None)
    if repo != section.get("repository") or repo != binaries.get("repository"):
        errors.append("renv.lock, binaries.toml and [r_packages].repository disagree")
    renv = lock["Packages"].get("renv", {}).get("Version")
    if renv != section.get("renv"):
        errors.append(f"renv.lock has renv {renv}, [r_packages].renv says {section.get('renv')}")
    for name, entry in lock["Packages"].items():
        if entry.get("Source") == "Repository":
            pinned = binaries.get("packages", {}).get(name, {})
            for platform in [*supported, "source"]:
                b = pinned.get(platform)
                if b is None:
                    errors.append(f"binaries.toml has no {platform} file for {name}")
                    continue
                if b["version"] != entry["Version"]:
                    errors.append(f"binaries.toml {name} {platform} is {b['version']}")
                from_cran = b.get("origin") == "cran" and b["url"].startswith(CRAN_BIN)
                if not (b["url"].startswith(f"{repo}/") or from_cran) or not SHA_RE.match(
                    b["sha256"]
                ):
                    errors.append(f"binaries.toml {name} {platform} has a bad url or sha256")
                assets.append(Asset("r_packages", f"{name} {platform}", b["url"], b["sha256"]))
        else:
            pkg = next((p for p in CELLAR if CELLAR_R_NAMES.get(p, p) == name), None)
            if entry.get("Source") != "Cellar":
                # renv fetches other sources (GitHub, ...) before it looks in the cellar.
                errors.append(f"renv.lock {name} must be Source: Cellar")
            if pkg is None:
                errors.append(f"renv.lock {name} is neither from P3M nor a pinned package")
            elif pins.get("packages", {}).get(pkg, {}).get("version") != entry["Version"]:
                errors.append(f"renv.lock {name} {entry['Version']} differs from the pin")
    extra = set(binaries.get("packages", {})) - set(lock["Packages"])
    if extra:
        errors.append(f"binaries.toml has packages not in renv.lock: {sorted(extra)}")
    return errors


def _check_python_envs(pins: dict[str, Any], root: Path) -> list[str]:
    errors: list[str] = []
    deck = pins.get("packages", {}).get("deckifyr", {})
    wheel = deck.get("wheel")
    for name, env in pins.get("python_envs", {}).items():
        lock_path = root / env["project"] / "uv.lock"
        if not lock_path.is_file():
            errors.append(f"[python_envs.{name}] {lock_path.relative_to(root)} does not exist")
            continue
        lock = load(lock_path)
        if wheel and env.get("deckifyr", False):
            entries = [p for p in lock.get("package", []) if p["name"] == "deckifyr"]
            hashes = [w.get("hash", "") for p in entries for w in p.get("wheels", [])]
            want = f"sha256:{deck['assets'].get(wheel, '')}"
            if want not in hashes:
                errors.append(f"[python_envs.{name}] uv.lock does not pin {wheel} with its hash")
    return errors


def _token() -> str | None:
    return os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")


def _open(asset: Asset) -> Any:
    headers = {"User-Agent": "opncafyr-verify-pins/1"}
    m = re.match(r"https://github\.com/([^/]+)/([^/]+)/releases/download/([^/]+)/(.+)$", asset.url)
    token = _token()
    if asset.private and m and token:
        owner, repo, tag, name = m.groups()
        api = f"https://api.github.com/repos/{owner}/{repo}/releases/tags/{tag}"
        req = urllib.request.Request(api, headers={**headers, "Authorization": f"Bearer {token}"})  # noqa: S310
        with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310
            release = json.load(resp)
        match = [a for a in release["assets"] if a["name"] == urllib.parse.unquote(name)]
        if not match:
            raise FileNotFoundError(f"{name} not in release {tag}")
        headers |= {"Authorization": f"Bearer {token}", "Accept": "application/octet-stream"}
        req = urllib.request.Request(match[0]["url"], headers=headers)  # noqa: S310
        return urllib.request.urlopen(req, timeout=300)  # noqa: S310
    req = urllib.request.Request(asset.url, headers=headers)  # noqa: S310
    return urllib.request.urlopen(req, timeout=300)  # noqa: S310


def check_network(assets: list[Asset]) -> tuple[list[str], int, int]:
    errors: list[str] = []
    ok = skipped = 0
    for asset in assets:
        if asset.private and not _token():
            print(f"SKIP {asset.section} {asset.label}: private, no GH_TOKEN", file=sys.stderr)
            skipped += 1
            continue
        digest = hashlib.sha256()
        try:
            with _open(asset) as resp:
                while chunk := resp.read(1 << 20):
                    digest.update(chunk)
        except (urllib.error.URLError, OSError) as exc:
            errors.append(f"{asset.section} {asset.label}: {asset.url}: {exc}")
            continue
        if digest.hexdigest() != asset.sha256:
            errors.append(f"{asset.section} {asset.label}: SHA-256 mismatch for {asset.url}")
        else:
            ok += 1
            print(f"ok   {asset.section} {asset.label}", file=sys.stderr)
    return errors, ok, skipped


def _check_wheels_current() -> list[str]:
    """wheels.toml must be what pin_wheels.py generates from the locks today."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("pin_wheels", ROOT / "scripts" / "pin_wheels.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if (ROOT / WHEELS).read_text("utf-8") != module.render():
        return [f"{WHEELS} is out of date: run scripts/pin_wheels.py"]
    return []


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--pins", type=Path, default=ROOT / "runtime" / "pins.toml")
    parser.add_argument("--network", action="store_true", help="download and hash every URL")
    parser.add_argument("--only", help="comma-separated section prefixes for --network")
    args = parser.parse_args(argv)

    root = args.pins.resolve().parents[1]
    errors, assets = check_structure(load(args.pins), root)
    if root == ROOT:
        errors += _check_wheels_current()
    print(f"structure: {len(assets)} pinned files, {len(errors)} problems")
    if args.network and not errors:
        if args.only:
            prefixes = tuple(p.strip() for p in args.only.split(","))
            assets = [a for a in assets if a.section.startswith(prefixes)]
        net_errors, ok, skipped = check_network(assets)
        errors += net_errors
        print(f"network: {ok} verified, {skipped} skipped, {len(net_errors)} failed")
    for error in errors:
        print(f"ERROR {error}", file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
