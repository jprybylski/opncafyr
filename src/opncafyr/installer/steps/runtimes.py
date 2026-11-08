"""Python, uv, Quarto, the runtime record and the payload copy."""

from __future__ import annotations

import shutil
import sys

from opncafyr.installer import proc
from opncafyr.installer.archives import extract, find_one
from opncafyr.installer.records import write_json
from opncafyr.installer.steps import WINDOWS, Context, venv_python


def python(ctx: Context) -> None:
    """Unpack python-build-standalone ourselves (not `uv python install`, spike S3)."""
    asset = ctx.asset("python")
    tmp = ctx.tmp / "python"
    shutil.rmtree(tmp, ignore_errors=True)
    extract(ctx.files[asset.path], tmp)
    (tmp / "python").rename(ctx.dir / "python")
    shutil.rmtree(tmp, ignore_errors=True)
    py = ctx.dir / "python" / ("python.exe" if WINDOWS else "bin/python3")
    if not py.is_file():
        raise FileNotFoundError(f"no interpreter at {py}")
    done = proc.capture(
        [py, "-I", "-c", "import sys, ssl; print(sys.version.split()[0])"], env=ctx.env()
    )
    got = done.stdout.decode().strip()
    if done.returncode != 0 or got != ctx.pins.python_version:
        raise RuntimeError(f"Python reports {got!r}, pinned {ctx.pins.python_version}")
    ctx.executables["python_base"] = py
    ctx.record("python", asset.version, [asset])


def uv(ctx: Context) -> None:
    asset = ctx.asset("uv")
    tmp = ctx.tmp / "uv"
    shutil.rmtree(tmp, ignore_errors=True)
    extract(ctx.files[asset.path], tmp)
    dest = ctx.dir / "uv"
    dest.mkdir(parents=True, exist_ok=True)
    for name in ("uv", "uvx", "uvw"):
        try:
            src = find_one(tmp, exe_name(name))
        except FileNotFoundError:
            if name == "uv":
                raise
            continue
        shutil.move(str(src), dest / src.name)
    shutil.rmtree(tmp, ignore_errors=True)
    uv_exe = dest / exe_name("uv")
    if not WINDOWS:
        uv_exe.chmod(0o755)
    done = proc.capture([uv_exe, "--version"], env=ctx.env())
    if done.returncode != 0 or asset.version not in done.stdout.decode():
        raise RuntimeError(f"uv reports {done.stdout.decode().strip()!r}, pinned {asset.version}")
    ctx.executables["uv"] = uv_exe
    ctx.record("uv", asset.version, [asset])


def exe_name(name: str) -> str:
    return f"{name}.exe" if WINDOWS else name


def quarto(ctx: Context) -> None:
    asset = ctx.asset("quarto")
    dest = ctx.dir / "quarto"
    extract(ctx.files[asset.path], dest)
    binary = find_one(dest, f"bin/{exe_name('quarto')}")
    if not WINDOWS:
        binary.chmod(0o755)
    ctx.executables["quarto"] = binary
    ctx.record("quarto", asset.version, [asset])


def runtime_record(ctx: Context) -> None:
    """``runtime/runtime.json`` (runtime-v1): the absolute executables the launcher may start."""
    names = {
        "python": venv_python(ctx.dir / "launcher"),
        "uv": ctx.executables["uv"],
        "rscript": ctx.executables["rscript"],
        "deckifyr_python": venv_python(ctx.dir / "tools"),
        "quarto": ctx.executables["quarto"],
    }
    for optional in ("soffice", "git"):  # only when installed (LibreOffice #8, Git #56)
        if optional in ctx.executables:
            names[optional] = ctx.executables[optional]
    doc = {
        "schema_version": 1,
        "executables": {k: str(v) for k, v in names.items()},
        "r_library": str(ctx.paths["r_library"]),
        "wheelhouse": str(ctx.paths["wheelhouse"]),
    }
    for key, path in names.items():
        if not path.is_file():
            raise FileNotFoundError(f"runtime executable {key} is missing: {path}")
    write_json(ctx.dir / "runtime" / "runtime.json", doc)


def installer_copy(ctx: Context) -> None:
    """Keep this install's payload, so repair and uninstall can run later."""
    ctx.payload.copy_to(ctx.dir / "installer")


def clear_quarantine(ctx: Context) -> None:
    """macOS: we verified every file, so drop a browser's quarantine flag on what we unpacked."""
    if sys.platform != "darwin":
        return
    for path in (ctx.dir, ctx.r_dir):
        if path.exists():
            proc.run(
                ["/usr/bin/xattr", "-dr", "com.apple.quarantine", path],
                env=ctx.env(),
                log=ctx.log("quarantine"),
                check=False,
            )
