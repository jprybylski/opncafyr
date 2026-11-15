"""datum: declared external data for a study, in a managed mode (#83, #85, ADR 0012).

datum (https://github.com/jprybylski/datum, MIT) keeps ``.data.yaml`` (what to fetch) and
``.data.lock.yaml`` (fingerprints) at the study root and has a CLI only. This adapter is
the launcher's side of it:

- **argv only.** Every datum process is ``[<pinned datum>, --json, --no-color, <policy
  flags>, --config, …, --lock, …, <command>, …]``. The binary comes from the runtime
  record, never from a study, and workspace files supply data, never commands.
- **datum enforces the policy too** (datum 1.7.0): ``--no-vcs`` (never runs Git or SVN,
  never edits ignore rules), ``--allow-types http,file,git``, ``--https-only`` and
  ``--git-https-only`` (no SSH), ``--env-allow`` (only the declared credentials),
  ``--root <study>`` with ``--target-prefix source-data`` and an app-owned ``--cache-dir``.
- **A policy first** (``config``): the same rules are checked by the launcher before any
  process starts, so a problem names the dataset and field in the form.
- **A minimal environment** (``datum_env``): ``core.proctree.child_env`` plus
  ``NO_COLOR``, the launcher's proxy and CA-bundle variables, and the credentials the
  user entered for that one job. Nothing else.
- **Never writes the study.** A check is ``--report-only``; a fetch is staged
  (``--stage-dir``/``--lock-out``) into a review folder (``staging``). Only a reviewed
  Apply (``promote``) changes study files.

Only ``opncafyr.workflows.datafetch`` imports this package; nothing in ``core`` or the NCA
workflow names it (``tests/test_boundaries.py``).
"""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

import yaml

from opncafyr.core.proctree import DEVNULL, PIPE, TimeoutExpired, child_env, spawn
from opncafyr.core.registry import JobContract, ToolDescriptor
from opncafyr.core.runtime import Runtime, RuntimeUnavailable
from opncafyr.paths import WORKSPACE_META_DIR, app_root

HERE = Path(__file__).resolve().parent
TOOL = "datum"
EXECUTABLE = "datum"

# Files datum keeps at the study root, and the launcher's own folder for this feature.
CONFIG_FILE = ".data.yaml"
LOCK_FILE = ".data.lock.yaml"
SOURCE_DATA = "source-data"
META = f"{WORKSPACE_META_DIR}/datafetch"
STATE_FILE = f"{META}/state.json"
JOURNAL_FILE = f"{META}/promote.json"
PREVIOUS_DIR = f"{META}/previous"
REVIEWS_DIR = f"{META}/reviews"

CHECK_CAPABILITY = "data_check"
FETCH_CAPABILITY = "data_fetch"
REVIEW_DOCUMENT = "review.json"

ALLOW_TYPES = "http,file,git"
# --env-allow with no names means "no restriction" in datum, so a config without
# credentials allows only this name, which the policy reserves (OPNCAFYR_*) and nothing sets.
NO_CREDENTIALS = "OPNCAFYR_NO_CREDENTIALS"

DATUM_TIMEOUT = "5m"  # per-request timeout inside datum; the job has its own limit
QUICK_TIMEOUT_S = 60.0
# Passed through from the launcher's environment, like core.gitfetch's proxy variables.
# SSL_CERT_FILE/SSL_CERT_DIR let a site with its own CA (a TLS-inspecting proxy) work.
PASS_THROUGH = (
    "HTTPS_PROXY",
    "https_proxy",
    "HTTP_PROXY",
    "http_proxy",
    "NO_PROXY",
    "no_proxy",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
)


class DatumError(RuntimeError):
    """A datum call that did not give a usable answer; ``message`` is for a person."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def cache_dir() -> Path:
    """datum's Git cache (``--cache-dir``): app-owned, never in a study."""
    return app_root() / "cache" / "datum"


def available(runtime: Runtime | None) -> bool:
    return runtime is not None and runtime.has(EXECUTABLE)


def executable(runtime: Runtime) -> Path:
    try:
        return runtime.executable(EXECUTABLE)
    except RuntimeUnavailable:
        raise DatumError(
            "unavailable",
            "datum isn't installed with this opncafyr, so external data can't be checked "
            "or fetched.",
        ) from None


def argv(
    runtime: Runtime,
    config: Path,
    lock: Path,
    command: str,
    *args: str,
    root: Path,
    credentials: Sequence[str] = (),
    yes: bool = False,
    confine: bool = True,
    extra: Sequence[str] = (),
) -> list[str]:
    """A datum command line. Global flags come before the command, as datum requires.

    ``root`` is where targets and relative ``file`` sources resolve (the study, or an
    empty scratch folder for lock edits). ``credentials`` are the ``${NAME}``s the config
    may use. ``confine`` adds ``--target-prefix source-data``. ``extra`` holds more global
    flags (``--report-only``, ``--stage-dir``, ``--verify-local``).
    """
    out = [
        str(executable(runtime)),
        "--json",
        "--no-color",
        "--no-vcs",
        "--allow-types",
        ALLOW_TYPES,
        "--https-only",
        "--git-https-only",
        "--cache-dir",
        str(cache_dir()),
        "--env-allow",
        ",".join(sorted(credentials)) or NO_CREDENTIALS,
        "--root",
        str(root),
    ]
    if confine:
        out += ["--target-prefix", SOURCE_DATA]
    out += [
        "--config",
        str(config),
        "--lock",
        str(lock),
        "--timeout",
        DATUM_TIMEOUT,
        "--concurrency",
        "1",
        *extra,
    ]
    if yes:
        out.append("--yes")
    return [*out, command, *args]


def datum_env(credentials: Mapping[str, str] | None = None) -> dict[str, str]:
    """The extra variables for a datum process (``child_env`` adds the minimal base).

    Credentials are added by name only for the job that needs them; ``config`` has already
    checked that every name is declared and none is reserved. datum's Git cache is passed
    as ``--cache-dir``, so no ``XDG_*`` variable is set.
    """
    env = {"NO_COLOR": "1"}
    env.update({k: v for k, v in os.environ.items() if k in PASS_THROUGH})
    env.update(credentials or {})
    return env


def process_env(runtime: Runtime, credentials: Mapping[str, str] | None = None) -> dict[str, str]:
    """The complete environment of a datum process that is not run as a job."""
    exe = executable(runtime)
    return child_env(datum_env(credentials), path_dirs=[exe.parent])


@dataclass(frozen=True)
class QuickResult:
    code: int
    stdout: str


def quick(
    runtime: Runtime, args: Sequence[str], cwd: Path, timeout: float = QUICK_TIMEOUT_S
) -> QuickResult:
    """Run a short, local datum command (``audit``, ``types``, ``schema``, lock edits).

    ``args`` is a complete argv from ``argv`` (or the binary plus a describing command).
    These commands never use the network and never write study files; ``cwd`` is a
    launcher-owned folder.
    """
    cwd.mkdir(parents=True, exist_ok=True)
    proc = spawn(list(args), env=process_env(runtime), cwd=cwd, stdout=PIPE, stderr=DEVNULL)
    out: list[bytes] = []

    def drain() -> None:
        stream = proc.popen.stdout
        if stream is not None:
            out.append(stream.read())

    reader = threading.Thread(target=drain, daemon=True)
    reader.start()
    try:
        code = proc.wait(timeout)
    except TimeoutExpired:
        proc.kill_tree()
        raise DatumError("timeout", "datum did not answer in time.") from None
    reader.join(timeout=10)
    return QuickResult(code, b"".join(out).decode("utf-8", "replace"))


def parse_json(text: str) -> Any:
    try:
        return json.loads(text)
    except ValueError:
        raise DatumError("bad_output", "datum's answer could not be read.") from None


# --- datum's own descriptions, cached per pinned binary ----------------------------------

_cache_lock = threading.Lock()
_info_cache: dict[tuple[str, str], Any] = {}


def _binary_key(runtime: Runtime) -> str:
    exe = executable(runtime)
    st = exe.stat()
    return f"{exe}:{st.st_size}:{st.st_mtime_ns}"


def _cached(runtime: Runtime, name: str, scratch: Path, args: list[str]) -> Any:
    key = (_binary_key(runtime), name)
    with _cache_lock:
        if key in _info_cache:
            return _info_cache[key]
    result = quick(runtime, [str(executable(runtime)), *args], scratch)
    if result.code != 0:
        raise DatumError("failed", f"datum could not describe its {name}.")
    value = parse_json(result.stdout)
    with _cache_lock:
        _info_cache[key] = value
    return value


def config_schema(runtime: Runtime, scratch: Path) -> dict[str, Any]:
    """datum's embedded JSON Schema for ``.data.yaml`` (``datum schema``)."""
    value = _cached(runtime, "schema", scratch, ["schema"])
    if not isinstance(value, dict):
        raise DatumError("bad_output", "datum's config schema could not be read.")
    return value


def source_types(runtime: Runtime, scratch: Path) -> list[dict[str, Any]]:
    """datum's source types and their fields (``datum --json types``)."""
    value = _cached(runtime, "types", scratch, ["--json", "types"])
    types = value.get("types") if isinstance(value, dict) else None
    if not isinstance(types, list):
        raise DatumError("bad_output", "datum's source types could not be read.")
    return types


def clear_caches() -> None:
    with _cache_lock:
        _info_cache.clear()


# --- job contracts -----------------------------------------------------------------------


@cache
def descriptor() -> ToolDescriptor:
    data = yaml.safe_load((HERE / "descriptor.yaml").read_text("utf-8"))
    return ToolDescriptor.from_mapping(data, "adapters/datum/descriptor.yaml", bundle=HERE)


def contract(capability: str) -> JobContract:
    jobs = descriptor().jobs or {}
    return jobs[capability]
