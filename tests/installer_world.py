"""A small, fake world for installer tests: pins, tiny pinned files and a payload.

The real staging steps start uv, R and Quarto; the tests replace them with fake steps that
write the same records, so the orchestration (verify, stage, commit, activate, fail,
repair, uninstall) runs on every CI OS in milliseconds.
"""

from __future__ import annotations

import hashlib
import io
import json
import sys
import tarfile
from dataclasses import dataclass, field
from pathlib import Path

PLATFORM = "macos-arm64"
BASE = "https://example.invalid"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass
class World:
    root: Path
    assets: Path  # flat folder of every pinned file (an --assets folder)
    bundle: Path  # the same files in bundle layout: assets/<component>/<file>
    payload: Path
    files: dict[str, bytes] = field(default_factory=dict)  # bundle path -> content


def _file(world: World, component: str, name: str, data: bytes | None = None) -> tuple[str, str]:
    data = f"fake {component} {name}".encode() if data is None else data
    world.files[f"assets/{component}/{name}"] = data
    (world.assets / name).write_bytes(data)
    dest = world.bundle / "assets" / component / name
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return f"{BASE}/{component}/{name}", sha(data)


GIT_ARCHIVE = "dugite-native-v2.56.0-a44aeca-macOS-arm64.tar.gz"


def git_archive(members: dict[str, bytes | None] | None = None) -> bytes:
    """A tiny tar.gz laid out like dugite-native's macOS archive (``./bin/git``,
    ``./libexec/git-core/``). A member with ``None`` content is a folder."""
    if members is None:
        members = {
            "bin/git": b"#!/bin/sh\necho fake git\n",
            "libexec/git-core/": None,
            "libexec/git-core/git-remote-https": b"fake helper\n",
            "etc/gitconfig": b"",
        }
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, data in members.items():
            info = tarfile.TarInfo(f"./{name.rstrip('/')}")
            info.mtime = 1_700_000_000
            if data is None:
                info.type, info.mode = tarfile.DIRTYPE, 0o755
                tf.addfile(info)
            else:
                info.size, info.mode = len(data), 0o755
                tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def make_world(
    tmp: Path,
    version: str = "0.0.1",
    *,
    source_build: bool = False,
    git: bytes | None = None,
) -> World:
    """``source_build`` also pins what ``repair --build-from-source`` needs (#34): a
    vendored opnca-r tarball (in the manifest too) and a standalone Rust archive. ``git``
    pins that archive as the best-effort ``[optional.git]`` (#56; see ``git_archive``)."""
    world = World(
        root=tmp,
        assets=tmp / "assets",
        bundle=tmp / "bundle",
        payload=tmp / f"payload-{version}",
    )
    world.assets.mkdir(parents=True)
    runtime = world.payload / "runtime"
    (runtime / "r").mkdir(parents=True)
    (runtime / "python").mkdir(parents=True)

    pins = ["schema_version = 1", "", "[platforms]", f'supported = ["{PLATFORM}"]']
    for name, filename, ver in (
        ("uv", "uv-aarch64-apple-darwin.tar.gz", "0.13.7"),
        ("python", "cpython-3.13.18.tar.gz", "3.13.18"),
        ("r", "R-4.6.2-arm64.pkg", "4.6.2"),
        ("quarto", "quarto-1.11.9-macos.tar.gz", "1.11.9"),
    ):
        url, digest = _file(world, name, filename)
        pins += ["", f"[{name}]", f'version = "{ver}"', 'license = "MIT"']
        pins += [f"[{name}.assets.{PLATFORM}]", f'url = "{url}"', f'sha256 = "{digest}"']

    manifest = {"schema_version": 1, "package": "opnca", "version": "0.3.1"}
    binary = "opnca_0.3.1_R4.6_macos-arm64.tgz"
    _, bin_sha = _file(world, "opnca-r", binary)
    entries = [
        {
            "asset": binary,
            "kind": "binary",
            "platform": "macos",
            "arch": "arm64",
            "r_minor": "4.6",
            "sha256": bin_sha,
        }
    ]
    vendored = "opnca_0.3.1-vendored.tar.gz"
    if source_build:
        _, vendored_sha = _file(world, "opnca-r", vendored)
        entries.append({"asset": vendored, "kind": "source-vendored", "sha256": vendored_sha})
    manifest |= {"source_commit": "c0ffee", "assets": entries}
    mdata = json.dumps(manifest).encode()
    world.files["assets/opnca-r/manifest.json"] = mdata
    (world.assets / "manifest.json").write_bytes(mdata)
    (world.bundle / "assets" / "opnca-r" / "manifest.json").write_bytes(mdata)
    pins += [
        "",
        "[packages.opnca-r]",
        'version = "0.3.1"',
        'license = "Apache-2.0"',
        "private = true",
        f'release_url = "{BASE}/opnca-r/"',
        f'manifest_sha256 = "{sha(mdata)}"',
        'source_commit = "c0ffee"',
        'r_minor = "4.6"',
        "[packages.opnca-r.assets]",
        f'"manifest.json" = "{sha(mdata)}"',
        f'"{binary}" = "{bin_sha}"',
    ]
    if source_build:
        pins += [f'"{vendored}" = "{vendored_sha}"']
        rust_url, rust_sha = _file(world, "rust", "rust-1.99.1-aarch64-apple-darwin.tar.xz")
        pins += ["", "[rust]", 'toolchain = "1.99.1"', "[rust.standalone]", 'license = "MIT"']
        pins += [f"[rust.standalone.assets.{PLATFORM}]", f'url = "{rust_url}"']
        pins += [f'sha256 = "{rust_sha}"', "", "[rtools]", 'version = "45"']
        pins += ['platforms = ["windows-x86_64"]']
    for pkg, ver in (
        ("quartifyr", "0.3.3"),
        ("reportifyr", "0.4.0"),
        ("pyro", "0.1.1"),
        ("gitneighbr", "0.2.0"),
    ):
        name = f"{pkg}_{ver}.tar.gz"
        _, digest = _file(world, pkg, name)
        pins += ["", f"[packages.{pkg}]", f'version = "{ver}"', 'license = "GPL-3.0-or-later"']
        pins += [f'release_url = "{BASE}/{pkg}/"', f"[packages.{pkg}.assets]"]
        pins += [f'"{name}" = "{digest}"']
    pins += ["", "[packages.deckifyr]", 'version = "0.3.6"', 'license = "GPL-3.0-or-later"']
    if git is not None:
        url, digest = _file(world, "git", GIT_ARCHIVE, git)
        pins += ["", "[optional.git]", 'status = "optional"', "default = true"]
        pins += ['version = "2.56.0"', 'license = "GPL-2.0-only AND MIT"']
        pins += [f"[optional.git.assets.{PLATFORM}]", f'url = "{url}"', f'sha256 = "{digest}"']
    pins += ["", "[r_packages]", 'lockfile = "runtime/r/renv.lock"']
    pins += ['binaries = "runtime/r/binaries.toml"']
    (runtime / "pins.toml").write_text("\n".join(pins) + "\n", "utf-8")

    r6_url, r6_sha = _file(world, "r-packages", "R6_2.6.1.tgz")
    r6_url = f"{BASE}/bin/macosx/sonoma-arm64/contrib/4.6/R6_2.6.1.tgz"
    lock = {
        "R": {"Version": "4.6.2"},
        "Packages": {
            "R6": {"Package": "R6", "Version": "2.6.1", "Source": "Repository"},
            "opnca": {"Package": "opnca", "Version": "0.3.1", "Source": "Cellar"},
        },
    }
    (runtime / "r" / "renv.lock").write_text(json.dumps(lock), "utf-8")
    (runtime / "r" / "binaries.toml").write_text(
        f'schema_version = 1\n[packages."R6".{PLATFORM}]\nversion = "2.6.1"\n'
        f'url = "{r6_url}"\nsha256 = "{r6_sha}"\n',
        "utf-8",
    )
    wheels = ["schema_version = 1"]
    for env, pkgs in (("launcher", ("fastapi",)), ("tools", ("deckifyr", "pyyaml"))):
        for pkg in pkgs:
            name = f"{pkg}-1.0-py3-none-any.whl"
            url, digest = _file(world, "wheels", name)
            wheels += ["", f"[[{env}.{PLATFORM}]]", f'name = "{pkg}"', 'version = "1.0"']
            wheels += [f'file = "{name}"', f'url = "{url}"', f'sha256 = "{digest}"']
    (runtime / "python" / "wheels.toml").write_text("\n".join(wheels) + "\n", "utf-8")

    write_payload(world.payload, version)
    return world


def write_payload(directory: Path, version: str) -> None:
    from opncafyr.installer.payload import digest_of

    wheel = directory / f"opncafyr-{version}-py3-none-any.whl"
    wheel.write_bytes(f"wheel {version}".encode())
    files = {
        p.relative_to(directory).as_posix(): sha(p.read_bytes())
        for p in sorted(directory.rglob("*"))
        if p.is_file() and p.name != "payload.json"
    }
    doc = {"schema_version": 1, "version": version, "files": files, "digest": digest_of(files)}
    (directory / "payload.json").write_text(json.dumps(doc), "utf-8")


def fake_steps(log: list[str] | None = None):
    """Staging steps that write what the real ones would, with real files as executables."""
    from opncafyr.installer.steps import runtimes

    def stage(ctx) -> None:
        if log is not None:
            log.append(ctx.install_id)
        bin_dir = ctx.dir / "bin"
        bin_dir.mkdir(parents=True, exist_ok=True)
        for name in ("python", "uv", "rscript", "deckifyr_python", "quarto"):
            exe = bin_dir / name
            exe.write_text("#!/bin/sh\n", "utf-8")
            ctx.executables[name] = exe
        (ctx.dir / "launcher" / ("Scripts" if sys.platform == "win32" else "bin")).mkdir(
            parents=True
        )
        lib = ctx.dir / "rlib"
        lib.mkdir()
        wh = ctx.dir / "wheelhouse"
        wh.mkdir()
        ctx.paths |= {"r_library": lib, "wheelhouse": wh}
        for asset in ctx.assets:
            if not asset.best_effort:  # its own step records it (Git, #56)
                ctx.record(asset.component, asset.version, [asset])
        (ctx.log_dir / "stage.log").write_text(
            "Authorization: Bearer ghp_abcdefghijklmnopqrstuvwxyz0123\n", "utf-8"
        )

    def record(ctx) -> None:
        from opncafyr.installer.records import write_json

        doc = {
            "schema_version": 1,
            "executables": {k: str(v) for k, v in ctx.executables.items()},
            "r_library": str(ctx.paths["r_library"]),
            "wheelhouse": str(ctx.paths["wheelhouse"]),
        }
        write_json(ctx.dir / "runtime" / "runtime.json", doc)

    return lambda: [
        ("stage", stage),
        ("runtime_record", record),
        ("installer_copy", runtimes.installer_copy),
    ]


def run(layout, payload_dir: Path, messages: list[str] | None = None, **opts) -> int:
    from opncafyr.installer import payload as payload_mod
    from opncafyr.installer.install import Installer, Options

    out = messages if messages is not None else []
    options = Options(skip_disk_check=True, **opts)
    return Installer(layout, payload_mod.load(payload_dir), options, out.append).run()
