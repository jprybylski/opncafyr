"""gitneighbr adapter (#56): Git version history for one workspace folder.

Starts gitneighbr's managed mode (``scripts/serve.R``, gitneighbr >= 0.2.0) under
``/apps/gitneighbr/``. Everything it runs comes from the installed runtime: ``rscript``
and the pinned portable ``git`` (never PATH git). gitneighbr runs that Git with no user
or system configuration, no hooks, filters or credential helpers, HTTPS only, and works
only on the exact folder it is given (see its ``vignette("launcher")``).

The launcher's proxy adds the session token on every request, so the page never holds
it; a second token, known only here, stops the server.
"""

from __future__ import annotations

import json
import secrets
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import httpx

from opncafyr.core.adapter import ChildActivity, ChildView, LaunchContext, LaunchSpec
from opncafyr.core.manifest import atomic_write_json, atomic_write_text

API_TOKEN_HEADER = "X-Gitneighbr-Token"  # noqa: S105 - a name, not a secret
SHUTDOWN_TOKEN_HEADER = "X-Gitneighbr-Shutdown-Token"  # noqa: S105 - a name, not a secret
API_TOKEN_ENV = "GITNEIGHBR_API_TOKEN"  # noqa: S105 - a name, not a secret
SHUTDOWN_TOKEN_ENV = "GITNEIGHBR_SHUTDOWN_TOKEN"  # noqa: S105 - a name, not a secret
READY_PREFIX = "GITNEIGHBR_READY "
FEATURES = ("local", "local,remote")
POLICY_FILE = "gitneighbr-policy.json"
# Where a portable Git keeps its helpers, relative to the folder above its executable.
# The pinned macOS build isn't relocatable on its own and needs this named.
EXEC_PATHS = ("libexec/git-core", "ucrt64/libexec/git-core", "mingw64/libexec/git-core")


def git_exec_path(git: Path) -> Path | None:
    """The helper folder of a portable Git, or None for a Git that finds its own."""
    root = git.parent.parent
    for rel in EXEC_PATHS:
        candidate = root / rel
        if candidate.is_dir():
            return candidate
    return None


TRUST_BEGIN = "# opncafyr: begin trusted study folder (rewritten at every launch)"
TRUST_END = "# opncafyr: end trusted study folder"


def trust_study_folder(config: Path, root: Path) -> None:
    """Let the managed Git open the selected study folder even if another user owns it (#62).

    Git refuses a repository that belongs to another user ("dubious ownership"). In a
    container the study folder is a mount whose owner often isn't the container's user.
    The trust is exactly the folder the person selected, resolved: one ``safe.directory``
    entry in the launcher-owned file that is the managed Git's only global configuration
    (``GIT_CONFIG_GLOBAL``). Never ``*``, never a parent folder, and nothing read from the
    workspace: the path comes from the session. gitneighbr still refuses anything that isn't
    that exact folder. Rewritten at every launch, so the entry for a previous study doesn't
    stay. Other entries in the file, such as the person's Git name and email, are kept.
    """
    value = root.resolve().as_posix()
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in value):
        raise ValueError("The study folder's path has a control character; Git can't trust it.")
    quoted = value.replace("\\", "\\\\").replace('"', '\\"')
    kept: list[str] = []
    in_block = False
    old = config.read_text(encoding="utf-8").splitlines() if config.is_file() else []
    for line in old:
        stripped = line.strip()
        if stripped == TRUST_BEGIN:
            in_block = True
            continue
        if in_block:
            if stripped == TRUST_END:
                in_block = False
                continue
            # A block cut short by a hand edit ends at the first line that isn't ours.
            if not stripped or stripped.lower() == "[safe]" or stripped.startswith("directory"):
                continue
            in_block = False
        kept.append(line)
    while kept and not kept[-1].strip():
        kept.pop()
    block = [TRUST_BEGIN, "[safe]", f'\tdirectory = "{quoted}"', TRUST_END]
    atomic_write_text(config, "\n".join([*kept, *([""] if kept else []), *block]) + "\n")


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


class GitneighbrWebApp:
    """One gitneighbr session for the open workspace.

    ``session`` keys (set by ``workflows/nca/history.py``):
    ``workspace_root`` (absolute, the open workspace; also tells the supervisor to stop
    this child when another workspace opens), ``git_config`` (an absolute launcher-owned
    file outside the workspace, where gitneighbr keeps the user's name and email),
    ``remote`` (bool: offer Send/Update) and ``policy`` (gitneighbr launcher policy).
    """

    def launch(self, ctx: LaunchContext, session: Mapping[str, Any]) -> LaunchSpec:
        rscript = ctx.runtime.executable("rscript")
        git = ctx.runtime.executable("git")
        library = ctx.runtime.require_r_library()
        serve = library / "gitneighbr" / "scripts" / "serve.R"
        if not serve.is_file():
            raise FileNotFoundError(f"gitneighbr serve.R not found in the runtime: {serve}")

        root = Path(str(session.get("workspace_root") or ""))
        if not root.is_absolute() or not root.is_dir():
            raise ValueError("gitneighbr session needs an absolute workspace_root folder.")
        config = Path(str(session.get("git_config") or ""))
        if not config.is_absolute() or _inside(config, root):
            raise ValueError("gitneighbr git_config must be absolute and outside the workspace.")
        config.parent.mkdir(parents=True, exist_ok=True)
        trust_study_folder(config, root)
        features = "local,remote" if session.get("remote") else "local"

        # The policy is launcher-written and kept outside the workspace, so nothing in the
        # study folder can change how its files are protected or presented.
        policy = ctx.workdir / POLICY_FILE
        atomic_write_json(policy, dict(session.get("policy") or {"schema_version": 1}))

        argv = [
            str(rscript),
            "--vanilla",
            str(serve),
            f"--repo={root}",
            f"--port={ctx.port}",
            f"--git={git}",
            f"--base-path={ctx.base_path}",
            f"--api-token-env={API_TOKEN_ENV}",
            f"--shutdown-token-env={SHUTDOWN_TOKEN_ENV}",
            f"--features={features}",
            f"--git-config={config}",
            f"--policy-file={policy}",
        ]
        exec_path = git_exec_path(git)
        if exec_path is not None:
            argv.append(f"--git-exec-path={exec_path}")
        env = {
            "R_LIBS": str(library),
            "R_LIBS_USER": str(library),
            "R_LIBS_SITE": "",
            API_TOKEN_ENV: ctx.token,
            SHUTDOWN_TOKEN_ENV: secrets.token_urlsafe(32),
        }
        return LaunchSpec(
            argv=argv,
            env=env,
            path_dirs=[rscript.parent, git.parent],
            auth_headers={API_TOKEN_HEADER: ctx.token},
            ready_line_prefix=READY_PREFIX,
        )

    def is_ready(self, child: ChildView, client: httpx.Client) -> bool:
        if child.ready_line is None:
            return False
        info = json.loads(child.ready_line[len(READY_PREFIX) :])
        if info.get("url") != child.context.base_url:
            return False
        resp = client.get(child.context.base_url + "api/v1/health")
        return resp.status_code == 200 and resp.json().get("data", {}).get("managed") is True

    def activity(self, child: ChildView, client: httpx.Client) -> ChildActivity:
        resp = client.get(child.context.base_url + "api/v1/health")
        resp.raise_for_status()
        # Git actions finish before their reply, so there is never unsaved state.
        return ChildActivity(unsaved=False, busy=bool(resp.json().get("data", {}).get("busy")))

    def stop(self, child: ChildView, client: httpx.Client) -> None:
        headers = {
            **child.spec.auth_headers,
            SHUTDOWN_TOKEN_HEADER: child.spec.env[SHUTDOWN_TOKEN_ENV],
        }
        client.post(child.context.base_url + "api/v1/shutdown", headers=headers)


def web_app() -> GitneighbrWebApp:
    return GitneighbrWebApp()
