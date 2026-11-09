"""The offline bundle builder (#36, option 2): every pinned file for one platform, plus
the installer, ready to carry across a firewall. The pinned Git for version history (#56) is
included by default and best effort: if it can't be fetched, the bundle is built without
it (and says so), and installs from it go on without version history.

    install.sh bundle --platform windows-x86_64 --out <dir> [--archive] [--mirror mirror.toml]
                      [--assets <dir>] [--with-libreoffice] [--without-git]
    opncafyr bundle   (the same, from an installed launcher)

It runs on any connected machine, for any supported platform, and fetches every file from
its original source (or the configured mirrors), checking each one against the pins. The
organisation assembles the bundle itself; nothing is redistributed by us (#43).

Layout of ``opncafyr-bundle-<version>-<platform>/``::

    assets/<component>/<file>   every pinned file
    installer/                  the installer payload (install.sh, install.bat, the wheel, pins)
    bundle.json                 manifest (bundle-manifest-v1): every file with its component,
                                version, SHA-256, size, source URL and licence
    SHA256SUMS                  coreutils format, every other file
    sbom.spdx.json              SPDX 2.3
    licenses.json               the licence inventory

``bundle.json`` and the file tree are deterministic: two builds from the same pins and
installer list the same hashes. The installer never trusts ``bundle.json`` or
``SHA256SUMS``; they are for people and audits. It checks every file against the pins
inside the installer, which are the trust root until #39 signs the payload.
"""

from __future__ import annotations

import io
import json
import os
import re
import tarfile
import time
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from opncafyr.installer.archives import link_or_copy
from opncafyr.installer.layout import Layout, remove_tree
from opncafyr.installer.payload import Payload
from opncafyr.installer.pins import Asset, Pins
from opncafyr.installer.platforms import SUPPORTED
from opncafyr.installer.records import write_json
from opncafyr.installer.sources import Cache, DownloadSource, LocalSource, resolve
from opncafyr.installer.verify import InstallError, sha256_bytes, sha256_file

MANIFEST = "bundle.json"
ZIP_TIME = (2020, 2, 2, 0, 0, 0)
Say = Callable[[str], None]


def bundle_name(version: str, platform: str) -> str:
    return f"opncafyr-bundle-{version}-{platform}"


def bundle_id(payload: Payload, platform: str, files: list[dict[str, Any]]) -> str:
    doc = {
        "payload": payload.digest,
        "platform": platform,
        "files": sorted((f["path"], f["sha256"]) for f in files),
    }
    return sha256_bytes(json.dumps(doc, sort_keys=True).encode())


# --- licences, read from the archives themselves --------------------------------------------

_DCF_FIELD = re.compile(r"^License:\s*(.+(?:\n[ \t].+)*)", re.MULTILINE)


def _r_license(path: Path, package: str) -> str:
    """The ``License`` field of an R package's DESCRIPTION, from a .tgz/.tar.gz or .zip."""
    member = f"{package}/DESCRIPTION"
    try:
        if path.name.endswith(".zip"):
            with zipfile.ZipFile(path) as zf:
                text = zf.read(member).decode("utf-8", errors="replace")
        else:
            with tarfile.open(path) as tf:
                fh = tf.extractfile(member)
                text = fh.read().decode("utf-8", errors="replace") if fh else ""
    except (OSError, KeyError, tarfile.TarError, zipfile.BadZipFile):
        return ""
    m = _DCF_FIELD.search(text)
    return " ".join(m.group(1).split()) if m else ""


def _wheel_license(path: Path) -> str:
    try:
        with zipfile.ZipFile(path) as zf:
            meta = next(n for n in zf.namelist() if n.endswith(".dist-info/METADATA"))
            text = zf.read(meta).decode("utf-8", errors="replace")
    except (OSError, StopIteration, zipfile.BadZipFile):
        return ""
    head = text.split("\n\n", 1)[0]
    for key in ("License-Expression", "License"):
        m = re.search(rf"^{key}:\s*(.+)$", head, re.MULTILINE)
        if m and m.group(1).strip() and m.group(1).strip().upper() != "UNKNOWN":
            value = m.group(1).strip()
            if len(value) < 120:  # a whole licence text pasted in the field: use classifiers
                return value
    classifiers = re.findall(r"^Classifier: License :: (?:OSI Approved :: )?(.+)$", head, re.M)
    return "; ".join(classifiers)


def license_of(asset: Asset, path: Path) -> str:
    if asset.license:
        return asset.license
    if asset.component == "r-packages":
        return _r_license(path, asset.name)
    if asset.component == "wheels":
        return _wheel_license(path)
    return ""


# --- the builder ----------------------------------------------------------------------------


def build(
    payload: Payload,
    platform: str,
    out: Path,
    *,
    say: Say,
    with_libreoffice: bool = False,
    with_git: bool = True,
    with_source_build: bool = False,
    allow_pending: bool = False,
    assets_dirs: list[Path] | None = None,
    rewrite: Callable[[str], str] | None = None,
    cacert: Path | None = None,
    proxy: str | None = None,
    archive: bool = False,
    cache_dir: Path | None = None,
    log: Path | None = None,
) -> Path:
    if platform not in SUPPORTED:
        raise InstallError(f"Unknown platform {platform}; use one of {', '.join(SUPPORTED)}")
    pins = Pins.load(payload.runtime)
    assets = pins.assets(
        platform,
        libreoffice=with_libreoffice,
        git=with_git,
        source_build=with_source_build,
        allow_pending=allow_pending,
    )
    cache = Cache(cache_dir or Layout.default().downloads)
    local = [LocalSource(d, cache) for d in (assets_dirs or [])]
    out.mkdir(parents=True, exist_ok=True)
    download = DownloadSource(
        cache,
        log=log or out / "bundle-downloads.log",
        cacert=cacert,
        proxy=proxy,
        rewrite=rewrite,
        user_agent=f"opncafyr-bundle/{payload.version}",
    )
    say(f"collecting {len(assets)} pinned files for {platform}")
    skipped: dict[str, str] = {}
    files = resolve(
        assets, cache=cache, local=local, download=download, progress=say, skipped=skipped
    )
    for component, problem in sorted(skipped.items()):
        say(f"warning: leaving out the optional {component}, which could not be fetched: {problem}")
    assets = [a for a in assets if not (a.best_effort and a.component in skipped)]

    dest = out / bundle_name(payload.version, platform)
    tmp = dest.with_name(dest.name + ".partial")
    remove_tree(tmp)
    entries = []
    for asset in sorted(assets, key=lambda a: a.path):
        src = files[asset.path]
        link_or_copy(src, tmp / asset.path)
        entries.append(
            {
                "path": asset.path,
                "component": asset.component,
                "name": asset.name,
                "version": asset.version,
                "sha256": asset.sha256,
                "size": src.stat().st_size,
                "source_url": asset.url,
                "license": license_of(asset, src) or "NOASSERTION",
                "optional": asset.optional,
            }
        )
    payload.copy_to(tmp / "installer")
    for name in ("install.sh", "install.bat"):
        if (tmp / "installer" / name).exists() and name == "install.sh":
            (tmp / "installer" / name).chmod(0o755)
    bid = bundle_id(payload, platform, entries)
    manifest = {
        "schema_version": 1,
        "bundle_id": bid,
        "opncafyr_version": payload.version,
        "platform": platform,
        "payload_digest": payload.digest,
        "options": {"libreoffice": with_libreoffice, "source_build": with_source_build},
        "trust": (
            "The installer checks every file against the pins inside its own payload, "
            "never against this list."
        ),
        "files": entries,
    }
    write_json(tmp / MANIFEST, manifest)
    (tmp / "licenses.json").write_text(json.dumps(_inventory(entries), indent=2) + "\n", "utf-8")
    (tmp / "sbom.spdx.json").write_text(json.dumps(_spdx(manifest), indent=2) + "\n", "utf-8")
    _sha256sums(tmp)
    remove_tree(dest)
    os.replace(tmp, dest)
    say(f"bundle {bid[:12]}: {dest}")
    if archive:
        path = write_archive(dest)
        say(f"archive: {path}")
        return path
    return dest


def _inventory(entries: list[dict[str, Any]]) -> dict[str, Any]:
    by_license: dict[str, int] = {}
    for e in entries:
        by_license[e["license"]] = by_license.get(e["license"], 0) + 1
    return {
        "schema_version": 1,
        "summary": dict(sorted(by_license.items())),
        "components": [
            {k: e[k] for k in ("component", "name", "version", "license", "path")} for e in entries
        ],
    }


def _spdx(manifest: dict[str, Any]) -> dict[str, Any]:
    packages = []
    for i, e in enumerate(manifest["files"], 1):
        packages.append(
            {
                "SPDXID": f"SPDXRef-Package-{i}",
                "name": e["name"],
                "versionInfo": e["version"] or "NOASSERTION",
                "packageFileName": e["path"],
                "downloadLocation": e["source_url"],
                "filesAnalyzed": False,
                "checksums": [{"algorithm": "SHA256", "checksumValue": e["sha256"]}],
                "licenseConcluded": "NOASSERTION",
                "licenseDeclared": e["license"] if _spdx_ok(e["license"]) else "NOASSERTION",
                "comment": f"component {e['component']}; declared licence: {e['license']}",
                "copyrightText": "NOASSERTION",
            }
        )
    return {
        "spdxVersion": "SPDX-2.3",
        "dataLicense": "CC0-1.0",
        "SPDXID": "SPDXRef-DOCUMENT",
        "name": f"opncafyr-bundle-{manifest['opncafyr_version']}-{manifest['platform']}",
        "documentNamespace": f"https://opncafyr.invalid/spdx/bundle/{manifest['bundle_id']}",
        "creationInfo": {
            "created": _created(),
            "creators": [f"Tool: opncafyr-bundle-{manifest['opncafyr_version']}"],
        },
        "packages": packages,
        "relationships": [
            {
                "spdxElementId": "SPDXRef-DOCUMENT",
                "relationshipType": "DESCRIBES",
                "relatedSpdxElement": p["SPDXID"],
            }
            for p in packages
        ],
    }


def _created() -> str:
    """The SBOM's creation time: ``SOURCE_DATE_EPOCH`` if set, else the archive's fixed
    entry time, so two builds from the same pins are byte-identical."""
    epoch = os.environ.get("SOURCE_DATE_EPOCH", "")
    if epoch.isdigit():
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(epoch)))
    return "{:04d}-{:02d}-{:02d}T{:02d}:{:02d}:{:02d}Z".format(*ZIP_TIME)


_SPDX_ID = re.compile(r"^[A-Za-z0-9.+-]+$")


def _spdx_ok(expr: str) -> bool:
    """A conservative check that a licence string is an SPDX expression."""
    tokens = expr.replace("(", " ").replace(")", " ").split()
    return bool(tokens) and all(t in ("AND", "OR", "WITH") or _SPDX_ID.match(t) for t in tokens)


def _sha256sums(root: Path) -> None:
    lines = []
    for path in sorted(p for p in root.rglob("*") if p.is_file() and p.name != "SHA256SUMS"):
        lines.append(f"{sha256_file(path)}  {path.relative_to(root).as_posix()}")
    (root / "SHA256SUMS").write_text("\n".join(lines) + "\n", "utf-8")


def write_archive(directory: Path) -> Path:
    """A deterministic ZIP64 of the bundle: sorted entries, fixed times, stored (the files
    are compressed already). Windows 11 and macOS open it without extra tools."""
    path = directory.with_name(directory.name + ".zip")
    tmp = path.with_name(path.name + ".partial")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_STORED, allowZip64=True) as zf:
        for file in sorted(p for p in directory.rglob("*") if p.is_file()):
            rel = f"{directory.name}/{file.relative_to(directory).as_posix()}"
            info = zipfile.ZipInfo(rel, ZIP_TIME)
            info.external_attr = (0o755 if file.name == "install.sh" else 0o644) << 16
            info.compress_type = zipfile.ZIP_STORED
            with file.open("rb") as src, zf.open(info, "w", force_zip64=True) as dst:
                while block := src.read(1 << 20):
                    dst.write(block)
    os.replace(tmp, path)
    return path


def read_manifest(location: Path) -> dict[str, Any] | None:
    """``bundle.json`` of a bundle folder or zip, for the report only (never for hashes)."""
    try:
        if location.is_dir():
            return json.loads((location / MANIFEST).read_text("utf-8"))
        with zipfile.ZipFile(location) as zf:
            name = next(n for n in zf.namelist() if n.rsplit("/", 1)[-1] == MANIFEST)
            return json.load(io.TextIOWrapper(zf.open(name), "utf-8"))
    except (OSError, ValueError, StopIteration, zipfile.BadZipFile):
        return None
