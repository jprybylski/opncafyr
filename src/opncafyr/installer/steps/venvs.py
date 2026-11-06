"""The launcher and tools Python environments, from verified wheels only (docs/offline.md I3, I4).

``uv sync --locked`` can't install from a wheelhouse (it fetches each locked URL), so the
installer writes a requirements file from ``runtime/python/wheels.toml`` (exact version and
SHA-256 per package) and runs ``uv pip install --no-index --require-hashes --no-deps``.
``--no-deps`` because the list is already the complete locked set; ``uv pip check``
then confirms it is consistent.
"""

from __future__ import annotations

from opncafyr.installer import proc
from opncafyr.installer.archives import link_or_copy
from opncafyr.installer.steps import Context, venv_python


def wheelhouse(ctx: Context) -> None:
    """Every verified wheel in one folder. Kept: report projects build their .venv from it."""
    dest = ctx.dir / "wheelhouse"
    dest.mkdir(parents=True, exist_ok=True)
    wheels = [a for a in ctx.assets if a.component == "wheels"]
    for asset in wheels:
        link_or_copy(ctx.files[asset.path], dest / asset.file)
    ctx.paths["wheelhouse"] = dest
    ctx.record("wheels", "", wheels)


def _requirements(ctx: Context, env: str) -> str:
    lines = []
    for w in ctx.pins.wheel_list(env, ctx.platform):
        lines.append(f"{w['name']}=={w['version']} --hash=sha256:{w['sha256']}")
    return "\n".join(lines) + "\n"


def _env(ctx: Context, name: str, log: str) -> None:
    uv = ctx.executables["uv"]
    venv = ctx.dir / name
    env = ctx.env()
    proc.run(
        [uv, "venv", "--quiet", "--python", ctx.executables["python_base"], venv],
        env=env,
        log=ctx.log(log),
    )
    req = ctx.tmp / f"{name}-requirements.txt"
    req.write_text(_requirements(ctx, name), "utf-8")
    py = venv_python(venv)
    proc.run(
        [uv, "pip", "install", "--python", py, "--no-index", "--find-links"]
        + [ctx.paths["wheelhouse"], "--require-hashes", "--no-deps", "--requirement", req],
        env=env,
        log=ctx.log(log),
    )
    if name == "launcher":
        # The launcher itself, from the payload's wheel (verified with the payload).
        proc.run(
            [uv, "pip", "install", "--python", py, "--no-index", "--no-deps"] + [ctx.payload.wheel],
            env=env,
            log=ctx.log(log),
        )
    proc.run([uv, "pip", "check", "--python", py], env=env, log=ctx.log(log))


def launcher_env(ctx: Context) -> None:
    _env(ctx, "launcher", "launcher_env")
    ctx.executables["launcher_python"] = venv_python(ctx.dir / "launcher")
    ctx.components["opncafyr"] = {
        "version": ctx.payload.version,
        "files": [
            {"file": ctx.payload.wheel.name, "sha256": ctx.payload.files[ctx.payload.wheel.name]}
        ],
    }


def tools_env(ctx: Context) -> None:
    _env(ctx, "tools", "tools_env")
    ctx.executables["deckifyr_python"] = venv_python(ctx.dir / "tools")
