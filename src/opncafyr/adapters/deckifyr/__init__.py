"""deckifyr adapter. See docs/plan.md for the issues that implement the rest of it (#33).

Implements the web-app part of ``opncafyr.core.adapter``: start, ready and stop for
``deckifyr serve`` (pinned in ``runtime/pins.toml``). Executables come from the installed
runtime, never from workspace content. Deck logic stays in deckifyr.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path, PurePath
from typing import Any

import httpx

from opncafyr.core.adapter import ChildView, LaunchContext, LaunchSpec
from opncafyr.core.supervisor import LaunchError

API_TOKEN_HEADER = "X-Deckifyr-Api-Token"  # noqa: S105 - a name, not a secret
API_TOKEN_ENV = "DECKIFYR_API_TOKEN"  # noqa: S105 - a name, not a secret
DEFAULT_JOB_TIMEOUT = 600
# Pinned tools passed with --trusted-binaries-only, so project files can't name others.
_TRUSTED_BINS = {"rscript": "--rscript-bin", "quarto": "--quarto-bin", "soffice": "--soffice-bin"}


class DeckifyrWebApp:
    """The deck editor for one project, served under ``/apps/deckifyr/``.

    ``session`` keys: ``project`` (absolute deck project folder, resolved by the caller
    with ``resolve_in_root``), optional ``presentation`` (a plain file name inside it) and
    ``job_timeout`` (seconds).
    """

    def launch(self, ctx: LaunchContext, session: Mapping[str, Any]) -> LaunchSpec:
        python = ctx.runtime.executable("deckifyr_python")
        project = Path(session.get("project") or "")
        if not project.is_absolute() or not project.is_dir():
            raise ValueError("deckifyr session 'project' must be an existing absolute folder.")
        presentation = str(session.get("presentation") or "presentation.yaml")
        if len(PurePath(presentation).parts) != 1 or presentation in {".", ".."}:
            raise ValueError("deckifyr session 'presentation' must be a file name.")
        timeout = int(session.get("job_timeout") or DEFAULT_JOB_TIMEOUT)

        argv = [
            str(python),
            "-I",  # isolated: no PYTHON* variables, user site or cwd on sys.path
            "-m",
            "deckifyr",
            "serve",
            f"--project={project}",
            f"--presentation={presentation}",
            "--host=127.0.0.1",
            f"--port={ctx.port}",  # fixed: with --port 0 the Host check ignores the port
            "--launcher=workspace",
            f"--root-path={ctx.base_path.rstrip('/')}",
            f"--api-token-env={API_TOKEN_ENV}",
            "--trusted-binaries-only",
            f"--job-timeout={timeout}",
        ]
        path_dirs = []
        for name, flag in _TRUSTED_BINS.items():
            if ctx.runtime.has(name):
                exe = ctx.runtime.executable(name)
                argv.append(f"{flag}={exe}")
                path_dirs.append(exe.parent)
        return LaunchSpec(
            argv=argv,
            env={API_TOKEN_ENV: ctx.token},
            path_dirs=path_dirs,
            auth_headers={API_TOKEN_HEADER: ctx.token},
        )

    def is_ready(self, child: ChildView, client: httpx.Client) -> bool:
        resp = client.get(child.context.base_url + "api/health")
        if resp.status_code != 200:
            return False
        health = resp.json()
        if health.get("project_error"):
            code = health["project_error"].get("code", "error")
            raise LaunchError(f"deckifyr could not load the project ({code}).")
        return bool(health.get("project_loaded"))

    def stop(self, child: ChildView, client: httpx.Client) -> None:
        # Save rather than lose unsaved edits when the launcher exits. #14 adds the
        # refuse-then-ask flow for an explicit Quit.
        client.post(
            child.context.base_url + "api/shutdown",
            headers=child.spec.auth_headers,
            json={"if_dirty": "save", "active_jobs": "cancel"},
        )


def web_app() -> DeckifyrWebApp:
    return DeckifyrWebApp()
