"""Staging steps. Each takes the ``Context`` and installs one part into ``versions/<id>``.

The order is fixed (``STEPS``); each step adds what it installed to
``ctx.components`` and the executables it provides to ``ctx.executables``. A best-effort
optional component (Git, #56) also reports its outcome in ``ctx.optional``; its step
never fails the install.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import Any

from opncafyr.installer import proc
from opncafyr.installer.layout import Layout
from opncafyr.installer.payload import Payload
from opncafyr.installer.pins import Asset, Pins

WINDOWS = sys.platform == "win32"


@dataclass
class Context:
    layout: Layout
    platform: str
    install_id: str
    pins: Pins
    payload: Payload
    assets: list[Asset]
    files: dict[str, Path]
    log_dir: Path
    offline: bool
    say: Callable[[str], None]
    with_libreoffice: bool = False
    with_git: bool = True  # #56: the pinned portable Git, best effort
    build_from_source: bool = False  # #34: opnca-r from its vendored source
    without_opnca: bool = False  # development install of a pending platform: no opnca-r
    executables: dict[str, Path] = field(default_factory=dict)
    components: dict[str, dict[str, Any]] = field(default_factory=dict)
    paths: dict[str, Path] = field(default_factory=dict)
    # Best-effort optional components: name -> {"status", "detail"} (install record).
    optional: dict[str, dict[str, str]] = field(default_factory=dict)

    @property
    def dir(self) -> Path:
        return self.layout.version_dir(self.install_id)

    @property
    def r_dir(self) -> Path:
        return self.layout.r_dir(self.install_id)

    @property
    def tmp(self) -> Path:
        return self.dir / "tmp"

    @cached_property
    def tmpdir(self) -> Path:
        """A temporary folder for children (never with a space on macOS: R refuses it)."""
        return proc.space_free_tmp(f"install-{self.install_id}", self.tmp / "t")

    def asset(self, component: str, name: str | None = None) -> Asset:
        hits = [
            a
            for a in self.assets
            if a.component == component and (name is None or a.name == name or a.file == name)
        ]
        if len(hits) != 1:
            raise LookupError(f"expected one {component}/{name} asset, found {len(hits)}")
        return hits[0]

    def file(self, component: str, name: str | None = None) -> Path:
        return self.files[self.asset(component, name).path]

    def env(self, *path_prepend: Path, **extra: str) -> dict[str, str]:
        env = proc.base_env(
            offline=self.offline,
            tmpdir=self.tmpdir,
            cache=self.layout.cache,
            path_prepend=path_prepend,
        )
        env.update(extra)
        return env

    def log(self, step: str) -> Path:
        return self.log_dir / f"{step}.log"

    def report_optional(self, name: str, status: str, detail: str) -> None:
        """Record a best-effort component's outcome: installed, failed or skipped."""
        self.optional[name] = {"status": status, "detail": detail}

    def record(self, name: str, version: str, assets: list[Asset], **extra: Any) -> None:
        self.components[name] = {
            "version": version,
            "files": [{"file": a.file, "sha256": a.sha256} for a in assets],
            **extra,
        }


def venv_python(venv: Path, *, windowed: bool = False) -> Path:
    if WINDOWS:
        return venv / "Scripts" / ("pythonw.exe" if windowed else "python.exe")
    return venv / "bin" / "python"


def steps() -> list[tuple[str, Callable[[Context], None]]]:
    """The staging steps in order: (name, function)."""
    from opncafyr.installer.steps import git, libreoffice, r, runtimes, venvs

    return [
        ("python", runtimes.python),
        ("uv", runtimes.uv),
        ("wheelhouse", venvs.wheelhouse),
        ("launcher_env", venvs.launcher_env),
        ("tools_env", venvs.tools_env),
        ("r", r.install_r),
        ("r_library", r.install_library),
        ("quarto", runtimes.quarto),
        ("libreoffice", libreoffice.install),
        ("git", git.install),
        ("runtime_record", runtimes.runtime_record),
        ("installer_copy", runtimes.installer_copy),
        ("quarantine", runtimes.clear_quarantine),
    ]
