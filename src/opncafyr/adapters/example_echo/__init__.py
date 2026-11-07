"""example_echo: a toy second tool that proves the core is tool-agnostic (#41).

It is the worked example in ``docs/adding-a-tool.md`` and is left out of release builds
(``pyproject.toml``). It has the two parts a tool can have:

- **A web app** (``server.py``), started by the supervisor and served under
  ``/apps/example_echo/``: ``EchoWebApp`` builds its command line, checks readiness and
  stops it.
- **A job** (``summarize.py``): ``summarize_request`` describes a job that summarizes a
  CSV's columns into ``summary.json`` (``summary-v1.schema.json``, bundled here). The
  job manager runs it; a workflow pack, or a test, submits it.

Nothing under ``opncafyr.core`` names this adapter. The registry finds it by scanning
``opncafyr.adapters`` for ``descriptor.yaml``. Its only executable is the pinned
``python`` from the installed runtime, and workspace files supply data, never commands.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import httpx
from jsonschema import Draft202012Validator

from opncafyr.core.adapter import ChildView, LaunchContext, LaunchSpec
from opncafyr.core.jobmanager import (
    CommandSpec,
    JobContext,
    JobPrepareError,
    JobRequest,
    ReviewResult,
)
from opncafyr.core.runtime import Runtime
from opncafyr.core.workspace import WorkspaceAccessError, resolve_in_root

HERE = Path(__file__).resolve().parent
SERVER = HERE / "server.py"
SUMMARIZE = HERE / "summarize.py"
SUMMARY_SCHEMA = HERE / "summary-v1.schema.json"
TOKEN_ENV = "EXAMPLE_ECHO_TOKEN"  # noqa: S105 - a variable name, not a secret
TOKEN_HEADER = "X-Example-Echo-Token"  # noqa: S105 - a header name, not a secret
READY_PREFIX = "ECHO_READY "
SUMMARY_TIMEOUT_S = 120


# --- web app -----------------------------------------------------------------------------


class EchoWebApp:
    """``server.py`` for one launch. It takes no session values."""

    def launch(self, ctx: LaunchContext, session: Mapping[str, Any]) -> LaunchSpec:
        argv = [
            str(ctx.runtime.executable("python")),
            "-I",  # isolated: no PYTHON* variables, user site or cwd on sys.path
            str(SERVER),
            "--port",
            str(ctx.port),
            "--base-path",
            ctx.base_path,
            "--token-env",
            TOKEN_ENV,
        ]
        return LaunchSpec(
            argv=argv,
            env={TOKEN_ENV: ctx.token},
            auth_headers={TOKEN_HEADER: ctx.token},
            ready_line_prefix=READY_PREFIX,
        )

    def is_ready(self, child: ChildView, client: httpx.Client) -> bool:
        return client.get(child.context.base_url + "api/health").status_code == 200

    def stop(self, child: ChildView, client: httpx.Client) -> None:
        client.post(child.context.base_url + "api/shutdown", headers=child.spec.auth_headers)


def web_app() -> EchoWebApp:
    """Called by ``core.registry.load_web_adapters`` because the descriptor has a web_app."""
    return EchoWebApp()


# --- job ---------------------------------------------------------------------------------


def summarize_command(runtime: Runtime, source: Path, out: Path) -> CommandSpec:
    """The job's command: the pinned Python, isolated, running the bundled script."""
    return CommandSpec(
        [str(runtime.executable("python")), "-I", str(SUMMARIZE), str(source), str(out)]
    )


def check_summary(path: Path) -> list[str]:
    """Problems with a written ``summary.json``; empty when it matches the bundled schema.

    A problem names where and which rule, never the value: jsonschema's own messages quote
    the offending value, and a job message must not carry data (ADR 0007).
    """
    try:
        doc = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return ["summary.json is missing or not JSON."]
    schema = json.loads(SUMMARY_SCHEMA.read_text("utf-8"))
    return [
        f"summary.json at /{'/'.join(map(str, e.absolute_path))} fails {e.validator!r}"
        for e in Draft202012Validator(schema).iter_errors(doc)
    ]


def _explain(code: int) -> str:
    if code == 3:
        return "The CSV could not be read."
    return f"The summary script stopped with exit code {code}."


def summarize_request(source: str, output: str) -> JobRequest:
    """A job that summarizes the workspace CSV ``source`` into the folder ``output``.

    Both paths are workspace-relative. The job manager stages the output and moves it
    into place only after a clean exit and a passing review.
    """

    def build(ctx: JobContext) -> CommandSpec:
        assert ctx.staging is not None  # noqa: S101 - the request has an output
        try:
            path = resolve_in_root(ctx.workspace, source)
        except WorkspaceAccessError:
            raise JobPrepareError("the CSV must be a file inside the study folder.") from None
        if not path.is_file():
            raise JobPrepareError("the CSV doesn't exist.")
        return summarize_command(ctx.runtime, path, ctx.staging)

    def review(ctx: JobContext) -> ReviewResult:
        assert ctx.staging is not None  # noqa: S101
        problems = check_summary(ctx.staging / "summary.json")
        return ReviewResult(not problems, "; ".join(problems[:3]))

    return JobRequest(
        kind="example_echo.summarize",
        label=f"Summarize {Path(source).name}",
        build=build,
        timeout_s=SUMMARY_TIMEOUT_S,
        output=output,
        review=review,
        explain=_explain,
    )
