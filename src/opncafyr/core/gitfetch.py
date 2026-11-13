"""Fetch a standards source from Git (#70, ADR 0011), on request only, with the pinned Git.

The same rules as version history (ADR 0008): only the Git in the installed runtime, never
one on ``PATH``; no user or system configuration, hooks, credential helpers, askpass, SSH or
LFS; HTTPS only, no redirects. Public repositories need nothing. For a private or GitHub
Enterprise repository the user pastes a token for this one fetch. It is held in memory, goes
to Git as an HTTP header through the environment of the one ``git fetch`` process (never in
the command line, a URL, the job record, a log or the study), and is forgotten when the job
ends. A URL with a user name or password in it is refused.

A fetch is a job (``standards.fetch``) whose output is ``.opncafyr/standards/.fetched/<id>/``:
- the job runs ``git fetch --depth=1 <url> <ref>`` into a bare repository in its own tmp;
- the review resolves the exact commit, lists the subfolder's tree and reads the wanted
  blobs into ``files/`` byte for byte (no checkout, so no attribute filters, line-ending
  conversion or links). For a bundle that is ``standards.yaml`` and the files it lists; for
  a tool-native template it is every regular file with a suffix the type accepts;
- ``fetch.json`` records repository, ref, subfolder and commit.
Then the same inspection as a local folder verifies everything. Nothing is installed by a
fetch.
"""

from __future__ import annotations

import base64
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from opncafyr.core import standards as S
from opncafyr.core.guidance import parse_guidance
from opncafyr.core.jobmanager import (
    CommandSpec,
    JobContext,
    JobPrepareError,
    JobRequest,
    ReviewResult,
)
from opncafyr.core.proctree import IS_WINDOWS, PIPE, TimeoutExpired, child_env, spawn
from opncafyr.core.runtime import Runtime, RuntimeUnavailable

GIT = "git"  # the runtime.json name of the pinned Git
FETCH_TIMEOUT_S = 300.0
LOCAL_TIMEOUT_S = 60.0
TOKEN_USER = "x-access-token"  # noqa: S105 - a user name GitHub accepts with any token
EXEC_PATHS = ("libexec/git-core", "ucrt64/libexec/git-core", "mingw64/libexec/git-core")
PROXY_ENV = ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "NO_PROXY", "no_proxy")
WINDOWS_SYSTEM_CONFIG = "[http]\n\tsslBackend = schannel\n\tschannelUseSSLCAInfo = false\n"

_HOST = r"[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?(?::[0-9]{1,5})?"
_NAME = r"[A-Za-z0-9_.-]{1,100}"
SHORT_RE = re.compile(rf"^(?:(?P<host>{_HOST})/)?(?P<owner>{_NAME})/(?P<repo>{_NAME})$")
URL_RE = re.compile(rf"^https://(?P<host>{_HOST})(?P<path>(?:/[A-Za-z0-9._~-]+)+)/?$")
REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,199}$")
TOKEN_RE = re.compile(r"^[A-Za-z0-9_.~+/=-]{8,1024}$")
_DEFAULT_HOST = "github.com"


class FetchError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class GitSource:
    url: str  # https, no user info
    repo: str  # as the user wrote it, for display and matching
    ref: str  # "HEAD" for the default branch
    subdir: str | None


def resolve_source(repo: str, ref: str | None, subdir: str | None) -> GitSource:
    """A Git source from ``[host/]owner/repo`` or an ``https://`` URL; HTTPS only."""
    text = repo.strip()
    if "@" in text:
        raise FetchError("bad_source", "Leave the user name and token out of the address.")
    if text.startswith("https://"):
        match = URL_RE.match(text)
        if match is None:
            raise FetchError("bad_source", "That is not an https:// repository address.")
        url = text.rstrip("/")
    elif "://" in text or text.startswith("git@"):
        raise FetchError("bad_source", "Only https:// repository addresses are supported.")
    else:
        match = SHORT_RE.match(text)
        if match is None:
            raise FetchError("bad_source", "Write the repository as owner/repo or host/owner/repo.")
        host = match.group("host") or _DEFAULT_HOST
        name = match.group("repo").removesuffix(".git")
        url = f"https://{host}/{match.group('owner')}/{name}.git"
    ref = (ref or "").strip() or "HEAD"
    if ref != "HEAD" and (
        not REF_RE.match(ref) or ".." in ref or ref.endswith((".lock", "/", "."))
    ):
        raise FetchError("bad_source", "That is not a branch, tag or commit name.")
    sub = (subdir or "").strip().strip("/") or None
    if sub is not None and not S.safe_relpath(sub):
        raise FetchError("bad_source", "The subfolder must be a plain path inside the repository.")
    return GitSource(url=url, repo=text, ref=ref, subdir=sub)


def exec_path(git: Path) -> Path | None:
    """A portable Git's helper folder, or None for a Git that finds its own."""
    root = Path(git).parent.parent
    return next((root / rel for rel in EXEC_PATHS if (root / rel).is_dir()), None)


def lockdown(git: Path, scratch: Path) -> tuple[list[str], dict[str, str]]:
    """``(argv prefix, extra environment)`` for a locked-down Git (ported from gitneighbr).

    The only configuration Git reads is two empty launcher-owned files (on Windows the
    system one selects the Windows certificate store, as Git for Windows does, so a company
    network's own certificates work).
    """
    scratch.mkdir(parents=True, exist_ok=True)
    empty = scratch / "empty"
    empty.mkdir(exist_ok=True)
    system = scratch / "system.gitconfig"
    system.write_text(WINDOWS_SYSTEM_CONFIG if IS_WINDOWS else "", "utf-8")
    global_config = scratch / "global.gitconfig"
    global_config.write_text("", "utf-8")
    config = {
        "core.hooksPath": str(empty),
        "init.templateDir": str(empty),
        "core.fsmonitor": "false",
        "core.askPass": "",
        "core.sshCommand": "false",
        "credential.helper": "",
        "submodule.recurse": "false",
        "fetch.recurseSubmodules": "false",
        "gc.auto": "0",
        "maintenance.auto": "false",
        "protocol.allow": "never",
        "protocol.https.allow": "always",
        "http.followRedirects": "false",
        "core.symlinks": "false",
    }
    prefix = [str(git)]
    helpers = exec_path(git)
    if helpers is not None:
        prefix.append(f"--exec-path={helpers}")
    for key, value in config.items():
        prefix += ["-c", f"{key}={value}"]
    env = {
        "GIT_CONFIG_SYSTEM": str(system),
        "GIT_CONFIG_GLOBAL": str(global_config),
        "GIT_ATTR_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_OPTIONAL_LOCKS": "0",
        "GCM_INTERACTIVE": "never",
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_LFS_SKIP_SMUDGE": "1",
    }
    return prefix, env


def auth_env(url: str, token: str | None) -> dict[str, str]:
    """The header that hands ``token`` to Git for ``url`` only, through the environment."""
    if not token:
        return {}
    basic = base64.b64encode(f"{TOKEN_USER}:{token}".encode()).decode("ascii")
    return {
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": f"http.{url}.extraHeader",
        "GIT_CONFIG_VALUE_0": f"Authorization: Basic {basic}",
    }


def secrets_of(token: str | None) -> list[str]:
    if not token:
        return []
    return [token, base64.b64encode(f"{TOKEN_USER}:{token}".encode()).decode("ascii")]


def scrub(path: Path, secrets: list[str]) -> None:
    """Remove ``secrets`` from a log file, in place."""
    if not secrets or not path.is_file():
        return
    text = path.read_text("utf-8", errors="replace")
    for secret in secrets:
        text = text.replace(secret, "‹token›")
    path.write_text(text, "utf-8")


def run_local(
    prefix: list[str], env: dict[str, str], cwd: Path, args: list[str], out: Any = PIPE
) -> bytes:
    """A local (no network) Git command; its stdout, or ``FetchError``."""
    proc = spawn([*prefix, *args], env=child_env(env), cwd=cwd, stdout=out, stderr=PIPE)
    try:
        stdout, _ = proc.popen.communicate(timeout=LOCAL_TIMEOUT_S)
    except TimeoutExpired:
        proc.kill_tree()
        raise FetchError("git_failed", "Git didn't answer in time.") from None
    if proc.popen.returncode != 0:
        raise FetchError("git_failed", f"Git stopped with exit code {proc.popen.returncode}.")
    return stdout or b""


def parse_tree(listing: bytes) -> dict[str, tuple[str, str, str, int]]:
    """``git ls-tree -r -z -l`` as ``{path: (mode, type, oid, size)}``."""
    out = {}
    for entry in listing.split(b"\0"):
        if not entry:
            continue
        meta, _, path = entry.partition(b"\t")
        mode, kind, oid, size = meta.decode("ascii").split(maxsplit=3)
        out[path.decode("utf-8", errors="replace")] = (
            mode,
            kind,
            oid,
            int(size) if size.strip().isdigit() else -1,
        )
    return out


def wanted_paths(
    entries: dict[str, tuple[str, str, str, int]],
    mode: str,
    manifest: bytes | None,
    suffixes: tuple[str, ...],
) -> list[str]:
    if mode == "bundle":
        doc, _ = parse_guidance(manifest or b"", S.SCHEMA, S.MANIFEST)
        paths = [S.MANIFEST]
        if doc is not None:
            paths += [f["path"] for c in doc["components"] for f in c.get("files", [])]
        return paths
    return [
        p
        for p, (_m, kind, _o, _s) in entries.items()
        if kind == "blob" and S.safe_relpath(p) and p.lower().endswith(suffixes)
    ]


def diagnose_text(stderr: str) -> str:
    low = stderr.lower()
    if "could not resolve host" in low or "failed to connect" in low or "timed out" in low:
        return "The repository's server can't be reached from here. Use a local copy instead."
    if (
        "authentication" in low
        or " 401" in low
        or " 403" in low
        or "terminal prompts disabled" in low
    ):
        return (
            "The repository refused access. Give a token that can read it (it is used for "
            "this fetch only), or check the address."
        )
    if "repository not found" in low or " 404" in low:
        return "There is no such repository, or it needs a token that can read it."
    if "couldn't find remote ref" in low or "not our ref" in low:
        return "The repository has no such branch, tag or commit."
    if "redirect" in low:
        return "The server redirected the request; give the repository's final https:// address."
    return "Git couldn't fetch the repository."


def fetch_request(
    source: GitSource,
    token: str | None,
    *,
    fetch_id: str,
    mode: str,
    suffixes: tuple[str, ...] = (),
    tool: str | None = None,
    native_type: str | None = None,
) -> JobRequest:
    """The ``standards.fetch`` job. ``mode`` is ``bundle`` or ``template``."""
    if mode not in ("bundle", "template"):
        raise ValueError("mode is bundle or template")
    if token is not None and not TOKEN_RE.match(token):
        raise FetchError("bad_token", "That doesn't look like a token (no spaces or line breaks).")
    # Held only until the job ends; the request object may outlive it in the job list.
    secrets = secrets_of(token)
    held: dict[str, Any] = {"token": token}

    def build(ctx: JobContext) -> CommandSpec:
        try:
            git = ctx.runtime.executable(GIT)
        except RuntimeUnavailable:
            raise JobPrepareError(
                "Git isn't installed with this opncafyr, so Git sources can't be fetched. "
                "Import a local copy instead."
            ) from None
        prefix, env = lockdown(git, ctx.tmpdir / "git")
        repo = ctx.tmpdir / "repo.git"
        try:
            run_local(prefix, env, ctx.tmpdir, ["init", "--bare", "-q", str(repo)])
        except FetchError as exc:
            raise JobPrepareError(exc.message) from None
        held.update(prefix=prefix, env=env, repo=repo)
        args = ["-C", str(repo), "fetch", "--depth=1", "--no-tags", "--no-recurse-submodules"]
        args += ["--", source.url, source.ref]
        auth = auth_env(source.url, held.get("token"))
        return CommandSpec([*prefix, *args], {**env, **_proxy(), **auth})

    def finish() -> None:
        held.clear()
        secrets.clear()

    def diagnose(ctx: JobContext, code: int) -> str | None:
        scrub(ctx.job_dir / "stderr.log", secrets)
        finish()
        try:
            text = (ctx.job_dir / "stderr.log").read_text("utf-8", errors="replace")
        except OSError:
            text = ""
        return diagnose_text(text)

    def review(ctx: JobContext) -> ReviewResult:
        scrub(ctx.job_dir / "stderr.log", secrets)
        try:
            return _extract(ctx, held, source, mode, suffixes, tool, native_type)
        except FetchError as exc:
            return ReviewResult(False, exc.message)
        finally:
            finish()

    return JobRequest(
        kind="standards.fetch",
        label="Fetch standards",
        build=build,
        review=review,
        diagnose=diagnose,
        timeout_s=FETCH_TIMEOUT_S,
        output=f"{S.STORE}/{S.FETCHED}/{fetch_id}",
    )


def _proxy() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if k in PROXY_ENV}


def _extract(
    ctx: JobContext,
    held: dict[str, Any],
    source: GitSource,
    mode: str,
    suffixes: tuple[str, ...],
    tool: str | None,
    native_type: str | None,
) -> ReviewResult:
    staging = ctx.staging
    assert staging is not None  # noqa: S101
    prefix, env, repo = held["prefix"], held["env"], held["repo"]
    git = ["-C", str(repo)]
    commit = run_local(
        prefix, env, ctx.tmpdir, [*git, "rev-parse", "--verify", "FETCH_HEAD^{commit}"]
    )
    commit_id = commit.decode("ascii").strip()
    if not re.fullmatch(r"[0-9a-f]{40}", commit_id):
        raise FetchError("git_failed", "Git didn't name the fetched commit.")
    treeish = f"{commit_id}:{source.subdir}" if source.subdir else commit_id
    try:
        listing = run_local(prefix, env, ctx.tmpdir, [*git, "ls-tree", "-r", "-z", "-l", treeish])
    except FetchError:
        raise FetchError("not_found", "The commit has no such subfolder.") from None
    entries = parse_tree(listing)
    files = staging / "files"
    files.mkdir()
    manifest = None
    if mode == "bundle":
        entry = entries.get(S.MANIFEST)
        if entry is None or entry[0] not in ("100644", "100755"):
            raise FetchError("not_found", f"The folder has no {S.MANIFEST}.")
        manifest = run_local(prefix, env, ctx.tmpdir, [*git, "cat-file", "blob", entry[2]])
    wanted = wanted_paths(entries, mode, manifest, suffixes)
    if len(wanted) > S.MAX_FILES:
        raise FetchError("too_large", "The source has too many files. Point the subfolder at it.")
    skipped = 0
    total = 0
    for path in wanted:
        entry = entries.get(path)
        if entry is None or entry[0] not in ("100644", "100755") or not S.safe_relpath(path):
            skipped += 1  # the inspection reports a listed file that isn't here
            continue
        total += max(entry[3], 0)
        if entry[3] > S.MAX_FILE_BYTES or total > S.MAX_TOTAL_BYTES:
            raise FetchError("too_large", "The source is too large to import.")
        target = files.joinpath(*path.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("wb") as out:
            run_local(prefix, env, ctx.tmpdir, [*git, "cat-file", "blob", entry[2]], out=out)
    meta = {
        "display": f"{source.repo}" + (f" ({source.subdir})" if source.subdir else ""),
        "repo": source.repo,
        "ref": source.ref,
        "commit": commit_id,
        "subdir": source.subdir,
        "mode": mode,
        "tool": tool,
        "type": native_type,
        "skipped": skipped,
    }
    (staging / "fetch.json").write_text(json.dumps(meta, indent=2), "utf-8")
    return ReviewResult(True)


def available(runtime: Runtime | None) -> bool:
    return runtime is not None and runtime.has(GIT)
