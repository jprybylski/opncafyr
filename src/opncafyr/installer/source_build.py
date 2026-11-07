"""``repair --build-from-source``: build opnca-r from its vendored source tarball (#34).

The default install uses opnca-r's prebuilt binary (ADR 0004). This is the exception
path, for when a binary can't be used: it builds ``opnca_<ver>-vendored.tar.gz``, which
carries every Rust crate, with no network access. Everything it uses is pinned and
checked like any other file, and everything it unpacks is removed afterwards:

- **Rust:** the standalone toolchain archive from ``[rust.standalone]``. Its ``rustc``,
  ``cargo`` and ``rust-std`` (and on Windows ``rust-mingw``) components are merged into
  one folder in Python, so no ``install.sh`` (which breaks on a path with a space) and no
  rustup run. ``CARGO_HOME`` and the build folder stay inside the build folder, never
  ``~/.cargo``; ``CARGO_NET_OFFLINE`` keeps cargo off the network.
- **Windows:** Rtools45 from ``[rtools]``, run as the user (``/CURRENTUSER``) into the
  build folder. Any uninstall entry it adds for that folder is removed again.
- **macOS:** the build needs Apple's Command Line Tools for the C compiler and linker.
  They need an administrator to install, so preflight stops with a clear message when
  they are missing. The ``/usr/bin/clang`` shim is never run (without the tools it opens
  an install dialog).

The build runs ``R CMD INSTALL --library=<the app's R library>`` with the pinned R; the
user's own R libraries are never on its path.
"""

from __future__ import annotations

import shutil
import sys
import tarfile
from pathlib import Path

from opncafyr.installer import opnca_release, proc
from opncafyr.installer.pins import OPNCA
from opncafyr.installer.verify import InstallError

WINDOWS = sys.platform == "win32"
RUST_COMPONENTS = ("rustc", "cargo", "rust-mingw")
RUST_STD_PREFIX = "rust-std-"
XCODE_SELECT = Path("/usr/bin/xcode-select")
CLT_MESSAGE = (
    "Building opnca-r from source on macOS needs Apple's Command Line Tools, and installing "
    "them needs an administrator. Ask IT to run `xcode-select --install`, then run "
    "`repair --build-from-source` again. The default install (without "
    "--build-from-source) needs no compiler."
)


def compiler_dirs(developer_dir: Path) -> list[Path]:
    """Where clang lives under ``xcode-select -p``: the CLT, or a full Xcode."""
    return [
        developer_dir / "usr" / "bin",
        developer_dir / "Toolchains" / "XcodeDefault.xctoolchain" / "usr" / "bin",
    ]


def check_command_line_tools() -> Path:
    """The folder holding clang, or ``InstallError`` with ``CLT_MESSAGE``."""
    if not XCODE_SELECT.is_file():
        raise InstallError(CLT_MESSAGE)
    done = proc.capture([XCODE_SELECT, "-p"], env=proc.base_env(offline=True), timeout=30)
    developer = Path(done.stdout.decode(errors="replace").strip())
    if done.returncode != 0 or not str(developer).strip() or not developer.is_dir():
        raise InstallError(CLT_MESSAGE)
    for folder in compiler_dirs(developer):
        if (folder / "clang").is_file():
            return folder
    raise InstallError(CLT_MESSAGE)


def _wanted(component: str) -> bool:
    return component in RUST_COMPONENTS or component.startswith(RUST_STD_PREFIX)


def merge_rust(archive: Path, work: Path, dest: Path) -> list[str]:
    """Unpack the standalone Rust archive and merge its needed components into ``dest``.

    Returns the merged component names. ``rustc`` and ``cargo`` must be among them.
    """
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    with tarfile.open(archive) as tar:
        tar.extractall(work, filter="data")
    tops = [p for p in work.iterdir() if p.is_dir()]
    if len(tops) != 1 or not (tops[0] / "components").is_file():
        raise InstallError(f"{archive.name} is not a Rust standalone archive")
    top = tops[0]
    names = [n.strip() for n in (top / "components").read_text("utf-8").splitlines()]
    merged = [n for n in names if n and _wanted(n)]
    if not {"rustc", "cargo"} <= set(merged):
        raise InstallError(f"{archive.name} has no rustc or cargo component")
    dest.mkdir(parents=True, exist_ok=True)
    for name in merged:
        for item in (top / name).iterdir():
            if item.name == "manifest.in":
                continue
            target = dest / item.name
            if item.is_dir():
                shutil.copytree(item, target, dirs_exist_ok=True)
            else:
                shutil.copy2(item, target)
    shutil.rmtree(work, ignore_errors=True)
    exe = ".exe" if WINDOWS else ""
    for tool in ("rustc", "cargo"):
        if not (dest / "bin" / f"{tool}{exe}").is_file():
            raise InstallError(f"the merged Rust toolchain has no {tool}")
    return merged


def short_path(path: Path) -> Path:
    """Windows: the 8.3 form of an existing ``path`` (no spaces where Windows has short
    names); elsewhere, or if there is none, ``path`` itself."""
    if not WINDOWS:
        return path
    import ctypes

    buf = ctypes.create_unicode_buffer(1024)
    n = ctypes.windll.kernel32.GetShortPathNameW(str(path), buf, len(buf))  # type: ignore[attr-defined]
    return Path(buf.value) if 0 < n < len(buf) else path


# Rtools' deepest file is about 175 characters below its folder, and Windows paths stop
# at 260, so the build folder's own path must stay short there.
MAX_WINDOWS_ROOT = 70


def build_root(base: Path) -> Path:
    """A short build folder with no space in its path, inside ``base``.

    Rtools, make and R's build rules break on a space, and a Windows user name or the
    macOS app root may contain one. On Windows the folder is ``<app root>\\sb`` in its
    8.3 form, short enough for Rtools' deepest files."""
    base.mkdir(parents=True, exist_ok=True)
    root = short_path(base) / ("sb" if WINDOWS else "source-build")
    if " " in str(root):
        raise InstallError(
            f"Building from source needs a folder without spaces, but {base} has one and "
            "Windows has no short name for it. Ask IT whether 8.3 short names can be "
            "enabled on this drive, or install opncafyr for a user name without a space."
        )
    if WINDOWS and len(str(root)) > MAX_WINDOWS_ROOT:
        raise InstallError(
            f"Building from source needs a short folder path, but {root} is "
            f"{len(str(root))} characters long (at most {MAX_WINDOWS_ROOT})."
        )
    return root


def build_env(
    root: Path, lib: Path, rust: Path, rtools: Path | None
) -> tuple[list[Path], dict[str, str]]:
    """``(PATH prefix, extra variables)`` for ``R CMD INSTALL`` of the vendored tarball."""
    path = [rust / "bin"]
    extra = {
        "CARGO_HOME": str(root / "cargo-home"),
        "CARGO_TARGET_DIR": str(root / "cargo-target"),
        "CARGO_NET_OFFLINE": "true",
        "R_LIBS": str(lib),
        "R_LIBS_USER": str(lib),
    }
    if rtools is not None:
        path += [rtools / "x86_64-w64-mingw32.static.posix" / "bin", rtools / "usr" / "bin"]
        extra["RTOOLS45_HOME"] = str(rtools)
    return path, extra


def install_rtools(ctx, dest: Path) -> Path:  # noqa: ANN001 - steps.Context
    """Run Rtools45's installer as the user into ``dest``; leave no uninstall entry."""
    from opncafyr.installer import r_registry

    before = set(r_registry.uninstall_subkeys())
    inno_log = ctx.log_dir / "rtools-inno.log"
    try:
        proc.run(
            [
                ctx.file("rtools"),
                "/VERYSILENT",
                "/SUPPRESSMSGBOXES",
                "/NORESTART",
                "/CURRENTUSER",
                "/NOICONS",
                f"/DIR={dest}",
                f"/LOG={inno_log}",
            ],
            env=ctx.env(),
            log=ctx.log("source_build"),
            timeout=1800,
        )
    except proc.ProcError as exc:
        tail = ""
        if inno_log.is_file():
            lines = inno_log.read_text("utf-8", errors="replace").splitlines()
            tail = "\n".join(lines[-15:])
        raise InstallError(f"Rtools' installer failed: {exc}\n{tail}") from None
    finally:
        for key in set(r_registry.uninstall_subkeys()) - before:
            if r_registry.owned_by(rf"{r_registry.UNINSTALL}\{key}", str(dest)):
                r_registry.delete(rf"{r_registry.UNINSTALL}\{key}")
    if not (dest / "usr" / "bin").is_dir():
        raise InstallError(f"Rtools didn't install into {dest}")
    return dest


def build_opnca(ctx, lib: Path) -> str:  # noqa: ANN001 - steps.Context
    """Build and install opnca-r from the vendored tarball into ``lib``; return its file."""
    pin = ctx.pins.pins["packages"][OPNCA]
    manifest = opnca_release.verify_manifest(ctx.file(OPNCA, "manifest.json"), pin)
    entry = opnca_release.select_asset(manifest, kind="source-vendored")
    tarball = ctx.asset(OPNCA, entry["asset"])
    source = ctx.files[tarball.path]
    opnca_release.verify_asset(source, entry, pin)

    # Windows: under the app root, where the path is shortest; macOS: the space-free temp.
    root = build_root(ctx.layout.root if WINDOWS else ctx.tmpdir)
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True)
    log = ctx.log("source_build")
    try:
        if sys.platform == "darwin":
            check_command_line_tools()
        staged = root / opnca_release.install_name(entry, pin["version"])
        shutil.copyfile(source, staged)
        ctx.say("  unpacking the Rust toolchain")
        rust = root / "rust"
        merged = merge_rust(ctx.file("rust"), root / "rust-src", rust)
        with log.open("a", encoding="utf-8") as fh:
            fh.write(f"rust components: {', '.join(merged)}\n")
        rtools = None
        if WINDOWS:
            ctx.say("  installing Rtools45 for this build")
            rtools = install_rtools(ctx, root / "rtools45")
        path, extra = build_env(root, lib, rust, rtools)
        r = ctx.executables["rscript"].with_name("R.exe" if WINDOWS else "R")
        ctx.say("  building opnca-r (several minutes)")
        proc.run(
            [r, "CMD", "INSTALL", f"--library={lib}", staged],
            env=ctx.env(*path, **extra),
            log=log,
            cwd=root,
            timeout=3600,
        )
        done = proc.capture(
            [
                ctx.executables["rscript"],
                "--vanilla",
                "-e",
                f"cat(as.character(packageVersion('opnca', lib.loc = {str(lib)!r})))",
            ],
            env=ctx.env(R_LIBS=str(lib), R_LIBS_USER=str(lib)),
            timeout=120,
        )
        got = done.stdout.decode(errors="replace").strip()
        if done.returncode != 0 or got != str(pin["version"]):
            raise InstallError(f"the source build installed opnca {got!r}, pinned {pin['version']}")
    finally:
        shutil.rmtree(root, ignore_errors=True)
    toolchains = [ctx.asset("rust")] + ([ctx.asset("rtools")] if WINDOWS else [])
    # Recorded as build tools: they were used for this install and then removed.
    ctx.record("rust", str(ctx.pins.pins["rust"]["toolchain"]), toolchains[:1])
    if WINDOWS:
        ctx.record("rtools", str(ctx.pins.pins["rtools"]["version"]), toolchains[1:])
    return tarball.file
