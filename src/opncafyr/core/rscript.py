"""How the launcher runs R: the pinned ``Rscript`` with the app-private library.

Every R program the launcher or a workflow runs goes through ``rscript_command``, so the
flags and environment are the same everywhere:

- ``--vanilla``: no site or user profile, no ``.Renviron``, no workspace restore or save.
  A project ``.Rprofile`` (renv bootstrap, repos) in the study folder never runs, which
  also keeps R offline (docs/offline.md R12).
- ``R_LIBS`` / ``R_LIBS_USER``: the pinned library from the runtime record.
- ``PATH``: only the folder holding ``Rscript``, plus the system folders
  (``core.proctree.child_env``).

Arguments reach the script as argv or environment variables, never by writing workspace
text into R code. Variable names must not start with ``OPNCAFYR_``: ``child_env`` drops
those.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

from opncafyr.core.jobmanager import CommandSpec
from opncafyr.core.runtime import Runtime

RSCRIPT = "rscript"
RSCRIPT_FLAGS = ("--vanilla",)


def rscript_command(
    runtime: Runtime,
    script: Path,
    *,
    args: Sequence[str] = (),
    env: Mapping[str, str] | None = None,
) -> CommandSpec:
    """The ``CommandSpec`` that runs ``script`` with the pinned R."""
    rscript = runtime.executable(RSCRIPT)
    library = str(runtime.require_r_library())
    extra = dict(env or {})
    for name in extra:
        if name.upper().startswith("OPNCAFYR_"):
            raise ValueError(f"R job variables may not use the OPNCAFYR_ prefix: {name}")
    extra.update({"R_LIBS": library, "R_LIBS_USER": library})
    return CommandSpec(
        argv=[str(rscript), *RSCRIPT_FLAGS, str(script), *args],
        env=extra,
        path_dirs=[rscript.parent],
    )
