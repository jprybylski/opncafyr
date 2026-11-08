"""example_echo: a toy second tool that proves the core is tool-agnostic (#41).

It is the worked example in ``docs/adding-a-tool.md`` and is left out of release builds
(``pyproject.toml``). It has the two parts a tool can have:

- **A web app** (``server.py``), started by the supervisor and served under
  ``/apps/example_echo/``: ``EchoWebApp`` builds its command line, checks readiness and
  stops it.
- **A job** (``summarize.py``): ``summarize_request`` describes a job that summarizes a
  CSV's columns into ``summary.json``. The descriptor's ``jobs.summarize_csv`` contract
  gives its timeout and its output schema, ``schemas/summary-v1.schema.json``, bundled
  here; the job manager checks ``summary.json`` against it before the job completes. A
  workflow pack, or a test, submits the job.

Nothing under ``opncafyr.core`` names this adapter. The registry finds it by scanning
``opncafyr.adapters`` for ``descriptor.yaml``. Its only executable is the pinned
``python`` from the installed runtime, and workspace files supply data, never commands.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from functools import cache
from pathlib import Path
from typing import Any

import httpx
import yaml

from opncafyr.core.adapter import ChildView, LaunchContext, LaunchSpec
from opncafyr.core.jobmanager import (
    CommandSpec,
    JobContext,
    JobPrepareError,
    JobRequest,
)
from opncafyr.core.registry import JobContract, ToolDescriptor
from opncafyr.core.runtime import Runtime
from opncafyr.core.workspace import WorkspaceAccessError, resolve_in_root

HERE = Path(__file__).resolve().parent
SERVER = HERE / "server.py"
SUMMARIZE = HERE / "summarize.py"
SUMMARY_CAPABILITY = "summarize_csv"
SUMMARY_DOCUMENT = "summary.json"
TOKEN_ENV = "EXAMPLE_ECHO_TOKEN"  # noqa: S105 - a variable name, not a secret
TOKEN_HEADER = "X-Example-Echo-Token"  # noqa: S105 - a header name, not a secret
READY_PREFIX = "ECHO_READY "


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


@cache
def summary_contract() -> JobContract:
    """The ``summarize_csv`` contract from this adapter's own descriptor.

    A workflow would take it from the registry (``tool.jobs["summarize_csv"]``); the toy
    reads its descriptor the same way the registry does, so tests need no app.
    """
    data = yaml.safe_load((HERE / "descriptor.yaml").read_text("utf-8"))
    descriptor = ToolDescriptor.from_mapping(
        data, "adapters/example_echo/descriptor.yaml", bundle=HERE
    )
    assert descriptor.jobs is not None  # noqa: S101 - the descriptor declares the job
    return descriptor.jobs[SUMMARY_CAPABILITY]


def check_summary(path: Path) -> list[str]:
    """Problems with a written ``summary.json``; empty when it matches the bundled schema.

    The job manager runs the same check on the staged output. A problem names where and
    which rule, never the value (ADR 0007).
    """
    try:
        doc = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return [f"{SUMMARY_DOCUMENT} is missing or not JSON."]
    return summary_contract().check_output(doc, SUMMARY_DOCUMENT)


def _explain(code: int) -> str:
    if code == 3:
        return "The CSV could not be read."
    return f"The summary script stopped with exit code {code}."


def summarize_request(source: str, output: str, contract: JobContract | None = None) -> JobRequest:
    """A job that summarizes the workspace CSV ``source`` into the folder ``output``.

    Both paths are workspace-relative. ``contract`` is the descriptor's ``summarize_csv``
    job contract (default: read from this adapter's descriptor); it gives the timeout and
    the schema ``summary.json`` must match. The job manager stages the output and moves it
    into place only after a clean exit and a passing check.
    """
    contract = contract or summary_contract()
    assert contract.timeout_s is not None  # noqa: S101 - the descriptor sets one

    def build(ctx: JobContext) -> CommandSpec:
        assert ctx.staging is not None  # noqa: S101 - the request has an output
        try:
            path = resolve_in_root(ctx.workspace, source)
        except WorkspaceAccessError:
            raise JobPrepareError("the CSV must be a file inside the study folder.") from None
        if not path.is_file():
            raise JobPrepareError("the CSV doesn't exist.")
        return summarize_command(ctx.runtime, path, ctx.staging)

    return JobRequest(
        kind="example_echo.summarize",
        label=f"Summarize {Path(source).name}",
        build=build,
        timeout_s=contract.timeout_s,
        output=output,
        output_document=SUMMARY_DOCUMENT,
        output_check=contract.check_output,
        explain=_explain,
    )
