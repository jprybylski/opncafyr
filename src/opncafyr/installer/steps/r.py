"""R itself and the app-private R library.

- **macOS** (docs/spikes/macos-install.md): expand CRAN's ``.pkg``, copy ``R.framework`` to
  ``~/Library/opncafyr/versions/<id>`` (no space: R and renv break on one), rewrite the
  framework path in R's text files only (CRAN's signatures stay valid), export ``RHOME`` in
  ``bin/R`` and ``DYLD_LIBRARY_PATH`` in ``etc/ldpaths`` (so a system R of the same minor
  can't hijack it), and write a ``bin/Rscript`` wrapper the runtime record can name.
- **Windows** (docs/spikes/windows-install.md): R's own installer in ``/CURRENTUSER`` mode
  without ``recordversion``. It writes one per-user uninstall entry whose name every per-user
  R 4.6.2 shares, so the entry is snapshotted first and put back afterwards: the user's own
  R is never touched.
- **Linux** (#65): Posit's Ubuntu 24.04 ``.deb`` is unpacked in Python (no dpkg, no root) into
  ``versions/<id>/r``, the original ``/opt/R/<version>`` is rewritten in R's text files, and
  ``Rscript``, whose binary has that path compiled in, is replaced by a wrapper that names
  ``RHOME`` (as on macOS). The ``.deb``'s system libraries are not installed: they are checked
  first (``linux_libs``). The library comes from Posit's binary tarballs, installed one at a
  time (parallel installs need ``make``) through ``R CMD INSTALL``.
- **Library, all OSes:** a local CRAN-like repository of the verified binaries, then base
  R's ``install.packages(type = "binary", dependencies = FALSE)``; then the cellar (the
  opnca-r binary, the pure-R quartifyr, reportifyr and pyro tarballs); then every version is
  checked against ``renv.lock``. No renv at install time: its restore needs the Command
  Line Tools on macOS and reaches for crandb.r-pkg.org when an index lacks a package.
- **The user's own R is never used** (#34): R runs with ``--vanilla``, ``R_LIBS`` and
  ``R_LIBS_USER`` set to the app's library and no site library, so packages are installed
  only into, and loaded only from, the app's library and R's own. A compiler guard
  (``R_MAKEVARS_USER``) makes any compile attempt fail: the default install builds
  nothing. ``repair --build-from-source`` builds opnca-r instead (``source_build``).
"""

from __future__ import annotations

import re
import shlex
import shutil
import sys
from importlib import resources
from pathlib import Path

from opncafyr.installer import opnca_release, proc
from opncafyr.installer.archives import link_or_copy
from opncafyr.installer.pins import CELLAR, OPNCA
from opncafyr.installer.steps import WINDOWS, Context

FRAMEWORK = "/Library/Frameworks/R.framework"
# R_MAKEVARS_USER for the default install: every compiler names a file that doesn't
# exist, so a package that tried to compile would fail instead of using a system compiler.
NO_COMPILER = "".join(
    f"{var} = /nonexistent/opncafyr-installs-no-compiled-code\n"
    for var in ("CC", "CXX", "CXX11", "CXX14", "CXX17", "CXX20", "FC", "F77", "OBJC", "OBJCXX")
)


def library_env(lib: Path) -> dict[str, str]:
    """R sees the app's library and its own, never the user's or a site library."""
    return {"R_LIBS": str(lib), "R_LIBS_USER": str(lib), "R_LIBS_SITE": ""}


def install_r(ctx: Context) -> None:
    asset = ctx.asset("r")
    if sys.platform == "darwin":
        rscript = _install_macos(ctx)
    elif WINDOWS:
        rscript = _install_windows(ctx)
    elif sys.platform.startswith("linux"):
        rscript = _install_linux(ctx)
    else:
        raise RuntimeError("R is installed on macOS, Windows and Linux only")
    done = proc.capture(
        [rscript, "--vanilla", "-e", "cat(R.version$major, R.version$minor, sep = '.')"],
        env=ctx.env(),
    )
    got = done.stdout.decode().strip()
    if done.returncode != 0 or got != ctx.pins.r_version:
        raise RuntimeError(
            f"R reports {got!r} (exit {done.returncode}), pinned {ctx.pins.r_version}: "
            + done.stderr.decode(errors="replace")[-500:]
        )
    ctx.executables["rscript"] = rscript
    ctx.paths["r_home"] = ctx.r_dir
    ctx.record("r", asset.version, [asset])


def _install_macos(ctx: Context) -> Path:
    r_dir = ctx.r_dir
    if " " in str(r_dir):
        raise RuntimeError(f"R's folder must not contain a space: {r_dir}")
    expanded = ctx.tmpdir / "rpkg"
    shutil.rmtree(expanded, ignore_errors=True)
    log = ctx.log("r")
    env = ctx.env()
    proc.run(["/usr/sbin/pkgutil", "--expand-full", ctx.file("r"), expanded], env=env, log=log)
    framework = r_dir / "R.framework"
    r_dir.mkdir(parents=True, exist_ok=True)
    proc.run(
        ["/usr/bin/ditto", expanded / "R-fw.pkg" / "Payload" / "R.framework", framework],
        env=env,
        log=log,
    )
    shutil.rmtree(expanded, ignore_errors=True)
    resources_dir = (framework / "Resources").resolve()
    relocate_text_files(resources_dir, FRAMEWORK, str(framework))
    ldpaths = resources_dir / "etc" / "ldpaths"
    with ldpaths.open("a", encoding="utf-8") as fh:
        fh.write(
            "\n## opncafyr: relocated R; load its own libraries even when a system R exists.\n"
            'DYLD_LIBRARY_PATH="${R_HOME}/lib${DYLD_LIBRARY_PATH:+:${DYLD_LIBRARY_PATH}}"\n'
            "export DYLD_LIBRARY_PATH\n"
        )
    bin_r = resources_dir / "bin" / "R"
    text = bin_r.read_text("utf-8")
    if "\nexport R_HOME\n" not in text:
        raise RuntimeError("R's bin/R has no `export R_HOME` line to extend")
    bin_r.write_text(
        text.replace("\nexport R_HOME\n", '\nexport R_HOME\nRHOME="${R_HOME}"; export RHOME\n', 1),
        "utf-8",
    )
    wrappers = ctx.dir / "bin"
    wrappers.mkdir(parents=True, exist_ok=True)
    home = shlex.quote(str(resources_dir))
    for name, target in (
        ("Rscript", resources_dir / "bin" / "Rscript"),
        ("R", resources_dir / "bin" / "R"),
    ):
        path = wrappers / name
        path.write_text(
            f'#!/bin/sh\nRHOME={home}\nexport RHOME\nexec {shlex.quote(str(target))} "$@"\n',
            "utf-8",
        )
        path.chmod(0o755)
    ctx.paths["r_home"] = resources_dir
    return wrappers / "Rscript"


def _install_linux(ctx: Context) -> Path:
    from opncafyr.installer.archives import extract_deb_tree

    r_dir = ctx.r_dir
    if " " in str(r_dir):
        raise RuntimeError(f"R's folder must not contain a space: {r_dir}")
    version = ctx.pins.r_version
    shutil.rmtree(r_dir, ignore_errors=True)
    count = extract_deb_tree(ctx.file("r"), f"opt/R/{version}", r_dir)
    if count < 100 or not (r_dir / "lib" / "R" / "bin" / "Rscript").is_file():
        raise RuntimeError(f"{ctx.asset('r').file} does not hold R {version} under opt/R/{version}")
    home = r_dir / "lib" / "R"
    relocate_text_files(
        r_dir,
        f"/opt/R/{version}",
        str(r_dir),
        folders=("bin", "lib/R/bin", "lib/R/etc", "lib/pkgconfig"),
    )
    # Rscript has /opt/R/<version>/lib/R compiled in, so it only works through a wrapper that
    # names RHOME. R starts its own child R processes through lib/R/bin/Rscript as well.
    inner = home / "bin" / "Rscript"
    inner.rename(home / "bin" / "Rscript.elf")
    inner.write_text(
        "#!/bin/sh\n# Relocated R (opncafyr): Rscript has its original prefix compiled in.\n"
        'RHOME="$(cd "$(dirname "$0")/.." && pwd -P)"\nR_HOME="$RHOME"\n'
        'export RHOME R_HOME\nexec "$RHOME/bin/Rscript.elf" "$@"\n',
        "utf-8",
    )
    inner.chmod(0o755)
    outer = r_dir / "bin" / "Rscript"
    outer.unlink(missing_ok=True)
    outer.write_text(
        f'#!/bin/sh\nexec {shlex.quote(str(inner))} "$@"\n',
        "utf-8",
    )
    outer.chmod(0o755)
    ctx.paths["r_home"] = home
    return outer


def relocate_text_files(
    resources_dir: Path,
    old: str,
    new: str,
    folders: tuple[str, ...] = ("bin", "etc"),
) -> list[Path]:
    """Replace ``old`` with ``new`` in the text files of the given folders (default R's ``bin``
    and ``etc``)."""
    changed = []
    needle = old.encode()
    for folder in (resources_dir / f for f in folders):
        for path in sorted(folder.rglob("*")):
            if path.is_symlink() or not path.is_file():
                continue
            data = path.read_bytes()
            if needle not in data or b"\0" in data[:8192]:
                continue
            path.write_bytes(data.replace(needle, new.encode()))
            changed.append(path)
    return changed


def _install_windows(ctx: Context) -> Path:
    from opncafyr.installer import r_registry

    dest = ctx.r_dir
    key = r_registry.uninstall_key(ctx.pins.r_version)
    snapshot = r_registry.snapshot(key)
    log = ctx.log("r")
    try:
        proc.run(
            [
                ctx.file("r"),
                "/VERYSILENT",
                "/SUPPRESSMSGBOXES",
                "/NORESTART",
                "/CURRENTUSER",
                "/NOICONS",
                f"/DIR={dest}",
                "/MERGETASKS=!desktopicon,!quicklaunchicon,!recordversion,!associate",
                f"/LOG={ctx.log_dir / 'r-inno.log'}",
            ],
            env=ctx.env(),
            log=log,
            timeout=1800,
        )
    finally:
        r_registry.restore(key, snapshot)
    rscript = dest / "bin" / "x64" / "Rscript.exe"
    if not rscript.is_file():
        raise FileNotFoundError(f"no Rscript.exe at {rscript}")
    ctx.paths["r_home"] = dest
    return rscript


def contrib_dir(url: str) -> str:
    """``bin/macosx/sonoma-arm64/contrib/4.6`` etc., from a pinned binary's URL.

    A Linux binary sits at the source path (``src/contrib``) of Posit's ``__linux__/noble``
    snapshot, which serves a binary to R's own User-Agent (#65).
    """
    m = re.search(
        r"/(bin/(?:macosx/[^/]+|windows)/contrib/[0-9.]+|src/contrib)/[^/]+$",
        url,
    )
    if m is None:
        raise ValueError(f"not a CRAN-like binary URL: {url}")
    return m.group(1)


def install_library(ctx: Context) -> None:
    binaries = [a for a in ctx.assets if a.component == "r-packages"]
    contribs = {contrib_dir(a.url) for a in binaries}
    if len(contribs) != 1:
        raise RuntimeError(f"R binaries come from more than one repository layout: {contribs}")
    contrib = contribs.pop()
    repo = ctx.tmp / "r-repo"
    shutil.rmtree(repo, ignore_errors=True)
    for asset in binaries:
        link_or_copy(ctx.files[asset.path], repo / contrib / asset.file)

    cellar = ctx.tmp / "r-cellar"
    shutil.rmtree(cellar, ignore_errors=True)
    cellar.mkdir(parents=True)
    opnca_file = None if ctx.build_from_source or ctx.without_opnca else _opnca_binary(ctx)
    if opnca_file is not None:
        link_or_copy(opnca_file[0], cellar / opnca_file[1])
    for name in CELLAR:
        link_or_copy(ctx.file(name), cellar / ctx.asset(name).file)

    lock_tsv = ctx.tmp / "renv-lock.tsv"
    rows = ["name\tversion\tsource"]
    for name, rec in sorted(ctx.pins.lock_records().items()):
        rows.append(f"{name}\t{rec['Version']}\t{rec.get('Source', '')}")
    lock_tsv.write_text("\n".join(rows) + "\n", "utf-8")

    script = ctx.tmp / "install_library.R"
    script.write_text(_r_script("install_library.R"), "utf-8")
    lib = ctx.dir / "rlib"
    lib.mkdir(parents=True, exist_ok=True)
    guard = ctx.tmp / "no-compiler.mk"
    guard.write_text(NO_COMPILER, "utf-8")
    proc.run(
        [
            ctx.executables["rscript"],
            "--vanilla",
            script,
            lib,
            repo,
            contrib,
            cellar,
            lock_tsv,
            opnca_file[1] if opnca_file is not None else "-",
        ],
        env=ctx.env(**library_env(lib), R_MAKEVARS_USER=str(guard)),
        log=ctx.log("r_library"),
        timeout=3600,
    )
    shutil.rmtree(repo, ignore_errors=True)
    shutil.rmtree(cellar, ignore_errors=True)
    opnca: list = []
    if ctx.without_opnca:
        ctx.say("warning: opnca-r is left out (development install): analysis can't run")
    elif opnca_file is None:
        from opncafyr.installer.source_build import build_opnca

        built = build_opnca(ctx, lib)
        opnca = [ctx.asset(OPNCA, f) for f in ("manifest.json", built)]
    else:
        opnca = [ctx.asset(OPNCA, f) for f in ("manifest.json", opnca_file[2])]
    ctx.paths["r_library"] = lib
    ctx.record("r-packages", "", binaries)
    if opnca:
        ctx.record(OPNCA, opnca[0].version, opnca)
    for name in CELLAR:
        ctx.record(name, ctx.asset(name).version, [ctx.asset(name)])


def _opnca_binary(ctx: Context) -> tuple[Path, str, str]:
    """Check opnca-r's manifest and pick the binary by its fields (ADR 0004)."""
    pin = ctx.pins.pins["packages"][OPNCA]
    manifest_path = ctx.file(OPNCA, "manifest.json")
    manifest = opnca_release.verify_manifest(manifest_path, pin)
    plat, arch = ctx.platform.split("-")
    entry = opnca_release.select_asset(
        manifest, platform=plat, arch=arch, r_minor=pin.get("r_minor")
    )
    binary = ctx.asset(OPNCA, entry["asset"])
    path = ctx.files[binary.path]
    opnca_release.verify_asset(path, entry, pin)
    return path, opnca_release.install_name(entry, pin["version"]), binary.file


def _r_script(name: str) -> str:
    return resources.files("opncafyr.installer").joinpath("r", name).read_text("utf-8")
