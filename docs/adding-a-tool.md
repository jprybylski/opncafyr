# Adding a tool

This guide is for a developer adding a new tool to opncafyr, for example an EDA plotting
tool or another modelling package. The core (`src/opncafyr/core/`) knows capabilities
and file contracts, not tool screens or calculations (docs/spec.md, "Reusable framework
principle"; ADR 0002). A tool is added with:

1. a **descriptor** (`descriptor.yaml`);
2. a **health check**, declared in the descriptor;
3. an **adapter** package: a web app adapter, job command builders, or both;
4. usually a **workflow pack** that gives users a way to start the tool's web app or
   jobs.

None of these steps changes `core/`, the home page or the workspace schema. The toy
adapter `src/opncafyr/adapters/example_echo/` is a working example of each part, and
`tests/test_example_echo.py` tests it. It is a development fixture: release wheels leave
it out (see [Keeping a tool out of releases](#keeping-a-tool-out-of-releases)).

Research use only: adding a tool to the launcher says nothing about whether its results
are right.

## Contents

- [The parts](#the-parts)
- [1. The descriptor](#1-the-descriptor)
- [2. The health check](#2-the-health-check)
- [3a. A web app](#3a-a-web-app)
- [3b. Jobs](#3b-jobs)
- [4. Giving users a way in: workflow packs](#4-giving-users-a-way-in-workflow-packs)
- [Security rules](#security-rules)
- [Shipping a tool](#shipping-a-tool)
- [Keeping a tool out of releases](#keeping-a-tool-out-of-releases)
- [Known limits](#known-limits)
- [Testing checklist](#testing-checklist)

## The parts

```
src/opncafyr/adapters/<id>/       the folder name must equal the descriptor's id
  descriptor.yaml                 what the tool is, needs and offers (tool-descriptor-v1)
  __init__.py                     the adapter: web_app(), command builders, parsers
  ...                             anything else the adapter bundles (scripts, schemas)
src/opncafyr/workflows/<pack>/    optional: routes and pages that use the adapter
tests/test_<id>.py
```

What happens at startup:

- `core.registry.load_descriptors()` scans the installed `opncafyr.adapters` package for
  `*/descriptor.yaml` and validates each against
  `src/opncafyr/schemas/tool-descriptor-v1.schema.json`. An invalid descriptor stops
  startup with an error naming the file and the field. Nothing in a study folder or the
  working directory can register a tool.
- `core.health.ToolHealth` runs each tool's health probe once, in the background.
- `core.registry.load_web_adapters()` imports `opncafyr.adapters.<id>` for every
  descriptor with a `web_app`, and calls its `web_app()`. The supervisor gets one entry per
  web app; the proxy serves it under its route.
- `core.registry.load_workflows()` scans `opncafyr.workflows` for packs.

What the user sees: the home page lists every descriptor as a tile (`GET /api/tools`):
its display name, capabilities, health and any features a missing optional requirement
turns off. A tile gets an **Open** button while its web app is running
(`GET /api/apps`). The static home page never names a tool; `tests/test_home_static.py`
checks that.

## 1. The descriptor

`src/opncafyr/adapters/example_echo/descriptor.yaml`:

```yaml
schema_version: "1.0"
id: example_echo
version: 1
display_name: Example echo
runtime: python
capabilities: [summarize_csv, echo_app]
executables: [python]
health: {kind: python_import, executable: python, target: csv}
jobs:
  summarize_csv: {output_schema: summary-v1, timeout_s: 120}
web_app:
  route: /apps/example_echo/
  readiness: /api/health
  max_body_bytes: 1048576
```

| Key | Required | Meaning |
|---|---|---|
| `schema_version` | yes | The descriptor format. Always `"1.0"` for `tool-descriptor-v1`. |
| `id` | yes | `^[a-z][a-z0-9_-]*$`. Must equal the folder name. Use underscores, not hyphens, so workflows can `from opncafyr.adapters import <id>`. |
| `version` | yes | The adapter's own version, an integer from 1. Raise it when the adapter's contract changes. |
| `display_name` | no | The tile's heading. Defaults to the id. |
| `runtime` | yes | `python`, `r` or `binary`. Informational. |
| `capabilities` | yes | What the tool can do, as snake_case names. The tile shows them; jobs, the project editor and optional requirements must name one of them. |
| `executables` | yes | The executables the tool needs, as **symbolic names** from the installed `runtime.json`, never paths. If one is missing, the tool is `unavailable`. See [Which Python, which R](#which-python-which-r). |
| `health` | yes | The health probe; see [2](#2-the-health-check). |
| `web_app` | no | `route`, `readiness` and optional `max_body_bytes`. Write `route` as `/apps/<id>/` with your own id: the proxy routes by tool id but gives the child the route as its base path, and nothing checks at startup that they agree (a mismatch shows as 404s). `readiness` is required; it documents the child's health path, and the adapter's `is_ready` does the check. The proxy refuses bodies over `max_body_bytes` (default 10 MiB) with 413. |
| `jobs` | no | `{<capability>: {output_schema, input_schema?, timeout_s?}}`: a job contract. Each schema name is looked up in the adapter's own `schemas/` folder (`adapters/<id>/schemas/<name>.schema.json`), then in `opncafyr.schemas`; a name in both places, or in neither, stops startup. Leave out `input_schema` when the input isn't a JSON document. Workflows read `timeout_s` from here, and a request can have the job manager check its output against `output_schema` (see [3b](#3b-jobs)). |
| `project_editor` | no | Lets the launcher's project-file editor open the tool's project files (#52): `capability`, `project_root` (a top-level study folder, never `OUTPUTS`), `file_profile` and optional `settings_schema`. The adapter package must then have `project_editor()`, returning a `core.adapter.ProjectEditor`. See `adapters/quartifyr/editor.py`. |
| `optional_requirements` | no | `[{name, executable, message, capabilities?}]`: an executable that some features need, such as LibreOffice for slide previews. When it is missing, the tile shows `message` and the capabilities it turns off; the tool stays `ok`. |

Unknown keys are refused. Optional keys may be added to `tool-descriptor-v1` in a
compatible way; anything else needs a new schema version.

### Which Python, which R

The installer writes `runtime.json` (`installer/steps/runtimes.py`). These names exist:

| Name | What it is | Use it for |
|---|---|---|
| `python` | The launcher's own Python environment | Standard-library tools only, like the toy. Never add packages to it for a tool. |
| `deckifyr_python` | The shared tools environment (deckifyr, quartifyr's Python side) | Python tools with dependencies. Add the tool and its packages to `runtime/python/tools/pyproject.toml` and `uv.lock`, then pin the wheels in `runtime/python/wheels.toml` (`scripts/pin_wheels.py`). |
| `rscript` | The pinned R, with the app's private R library | R tools. Add packages to `runtime/r/renv.lock` and their binaries to `runtime/r/binaries.toml`. |
| `uv`, `quarto` | The pinned uv and Quarto | Tools that drive them. |
| `soffice` | LibreOffice, only when the user installed it | An `optional_requirements` entry, never a required executable. |

`scripts/verify_pins.py` must pass after any pin change, and the offline bundle carries
whatever the pins list (`docs/offline.md`).

## 2. The health check

The descriptor declares the probe; core builds and runs the command. An adapter can't
supply its own probe command.

| `kind` | Command core runs | `target` |
|---|---|---|
| `python_import` | `<executable> -I -c "import <target>"` | a dotted module name |
| `r_package` | `<executable> --vanilla -e "library(<target>)"`, with `R_LIBS` set to the app's R library | an R package name |
| `version_flag` | `<executable> --version` | not allowed |

`health.executable` must be one of `executables`. The probes run one after another at
startup, each with the allow-listed child environment and a 60-second limit. Their output
is never shown; the tile says `ok` or gives a short reason. Health is information for the
user: nothing stops a workflow from starting an `unavailable` tool, so a workflow should
check `app.state.health.status(<id>)` and say why it can't start.

## 3a. A web app

A tool with a browser UI runs as a **child process** on its own loopback port. The
launcher's proxy serves it under `/apps/<id>/` on the launcher's origin, without
rewriting anything, so the child itself has to serve under that base path.

**Before you start:** the proxy forwards HTTP only. **WebSockets are not proxied**, so
frameworks that need them (Shiny, Streamlit, Dash, Bokeh server) don't work behind it yet.
A proxied request may take up to 300 seconds.

**A child app is trusted like launcher code.** Its pages share the launcher's origin, so
a script on them can read the launcher's page and call any launcher API with the user's
session. A child must therefore never serve data-derived HTML or script (column names,
cell values, user-written text) without escaping it, and must never load third-party
script. A cross-site scripting bug in a child is a bug in the launcher.

The adapter package's `web_app()` returns an object with the `core.adapter.WebAppAdapter`
methods. From `adapters/example_echo/__init__.py`:

```python
class EchoWebApp:
    def launch(self, ctx: LaunchContext, session: Mapping[str, Any]) -> LaunchSpec:
        argv = [
            str(ctx.runtime.executable("python")),
            "-I",
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

    def is_ready(self, child, client) -> bool:
        return client.get(child.context.base_url + "api/health").status_code == 200

    def stop(self, child, client) -> None:
        client.post(child.context.base_url + "api/shutdown", headers=child.spec.auth_headers)


def web_app() -> EchoWebApp:
    return EchoWebApp()
```

- **`launch(ctx, session)`** returns a `LaunchSpec`. `argv[0]` comes from
  `ctx.runtime`, never from `session`. `ctx` gives the port, the base path, a
  launcher-owned working folder and temp folder, and a fresh per-launch token. `session`
  holds values the calling workflow has already checked, such as an absolute project
  folder resolved with `resolve_in_root`. Check each one again (absolute, exists, the
  right type) and raise `ValueError` with a short message if it's wrong. Put a value only
  into a fixed flag (`f"--project={project}"`), never as its own argv item or as a flag
  name, so a value can't become an option.
- **The token.** Give the child the token through an environment variable, and list it
  in `auth_headers`. The proxy adds those headers to every request it forwards, and
  removes any copy the browser sends. The child should refuse API calls without the
  token, so a page on another site can't drive the child's port directly.
- **Readiness.** `ready_line_prefix` (optional) makes the supervisor wait for a stdout
  line starting with it. Then it polls `is_ready` until it returns `True`, or until the
  ready timeout. `is_ready` can raise `core.supervisor.LaunchError` with a message for
  the user, as deckifyr's does when it can't load a project.
- **`stop`** asks the child to stop cleanly. The supervisor waits a grace period, then
  stops the whole process tree.
- **`start` replaces.** `supervisor.start(<id>, session)` stops a running child of the
  same tool first, then blocks until the new one is ready (up to 60 seconds). It raises
  `KeyError` for an unknown tool, `LaunchError` if the child doesn't become ready, and
  whatever `launch` raises (`ValueError`). Callers catch these.
- **Logs.** The child's stdout and stderr go, redacted, to
  `<app root>/logs/children/<id>.log`. Don't print data there.
- **Unsaved work (optional).** An adapter can also have
  `activity(child, client) -> ChildActivity(unsaved, busy)`. Unsaved edits then block
  idle shutdown, and Quit asks first. See `DeckifyrWebApp.activity`.

What the child must do:

- bind to `127.0.0.1` on the given port only;
- serve every page, asset and API under the base path, with relative URLs;
- require the token on every route except a health route: pages and files can hold data
  too, and the proxy adds the token to every request it forwards;
- refuse a `Host` other than `127.0.0.1:<port>` (the proxy sends that) and a
  `Sec-Fetch-Site` other than `same-origin` or `none`, so another site can't drive the
  child's port directly;
- set no CORS headers, and not rely on cookies: the proxy drops `Cookie` and
  `Set-Cookie`.

The proxy also drops `Origin` and `Referer`, adds `frame-ancestors 'self'` (the launcher
shows the app in an iframe), and refuses bodies over `max_body_bytes`. Every request to
`/apps/<id>/` passes the launcher's own checks first: Host, session cookie and, for
state-changing requests, a same-origin `Origin`. Before relying on a real tool under a
prefix, run the base-path check from #16 (`docs/spikes/basepath.md`).

The toy's child, `adapters/example_echo/server.py`, is about 120 lines of standard
library code that does all of the above.

## 3b. Jobs

A job is a command the launcher runs for the user in the background: a check, a render, a
build. The job manager (`core/jobmanager.py`) owns the process: its state machine, logs,
time limit, cancellation of the whole process tree, and moving output into place. An
adapter supplies **commands**; a workflow pack submits **requests**.

From `adapters/example_echo/__init__.py`:

```python
def summarize_command(runtime: Runtime, source: Path, out: Path) -> CommandSpec:
    return CommandSpec(
        [str(runtime.executable("python")), "-I", str(SUMMARIZE), str(source), str(out)]
    )


def summarize_request(source: str, output: str, contract: JobContract | None = None) -> JobRequest:
    contract = contract or summary_contract()  # the descriptor's jobs.summarize_csv

    def build(ctx: JobContext) -> CommandSpec:
        try:
            path = resolve_in_root(ctx.workspace, source)
        except WorkspaceAccessError:
            raise JobPrepareError("the CSV must be a file inside the study folder.") from None
        return summarize_command(ctx.runtime, path, ctx.staging)

    return JobRequest(
        kind="example_echo.summarize",
        label=f"Summarize {Path(source).name}",
        build=build,
        timeout_s=contract.timeout_s,
        output=output,
        output_document="summary.json",
        output_check=contract.check_output,
        explain=_explain,
    )
```

- **`CommandSpec(argv, env, path_dirs)`**: `argv[0]` must be `runtime.executable(<name>)`
  for a name in your descriptor. Code enforces only that it is an absolute path to an
  existing file and that no shell is used; the rest is a review rule (see
  [Security rules](#security-rules)). `env` is added to the allow-listed environment
  (`OPNCAFYR_*` and other denied variables are removed, and setting `PATH` here raises
  `ValueError`). `PATH` is the directories in `path_dirs` followed by the system
  minimum: the runtime's own folders are not on it unless you list them. Point tool
  caches at the job's temp folder (`ctx.tmpdir`), e.g. `MPLCONFIGDIR`.
- **`JobRequest`** fields:
  - `kind` (`<tool>.<what>`), `label` (shown to the user) and `timeout_s` are required.
  - `output`: a workspace-relative folder. The command writes into `ctx.staging`, which
    already holds a `.opncafyr-job` owner marker, so a tool that needs an empty folder
    should write into a subfolder. The folder is moved into place only after a clean exit
    and a passing `review`, and **it must not exist beforehand**: completed outputs are
    never replaced, so give each job a fresh folder (e.g. with a new id). A failed or
    cancelled job never leaves an output that looks complete; its `<output>.staging/`
    stays next to it for diagnosis.
  - `cwd`: a workspace-relative working folder. By default the job runs in its own
    `work/` folder, which is deleted afterwards.
  - `build(ctx)` returns the command. It runs just before the job starts, with every path
    in `ctx` already resolved. Raise `JobPrepareError("…")` to fail with a message for the
    user; the job's label is put in front of it. Anything else `build` raises, such as
    `WorkspaceAccessError` from `resolve_in_root`, becomes a generic "could not be
    prepared", so catch it and raise `JobPrepareError` instead.
  - `output_document` and `output_check`: a plain `*.json` file name in the output folder
    and a function `(document, name) -> list[str]` of problems, usually the job
    contract's `JobContract.check_output`. The job manager reads and checks the document
    in staging before `review`; any problem fails the job (`review_failed`). The
    contract's problems name the place and the rule, never the value.
  - `review(ctx)`: any other check of the staged output. It may also add files to the
    staging folder. Its message, like every job message, must not quote data:
    jsonschema's own messages quote the bad value, so report the path and the rule
    instead (`core.manifest.schema_problems` does this).
  - `progress(line)`: turn a stdout line into a progress message. By default, lines
    starting with `OPNCAFYR-PROGRESS: ` are used.
  - `progress`, `explain` and `diagnose` messages are cut to 300 characters, without
    control characters.
  - `explain(code)` and `diagnose(ctx, code)`: turn a failing exit code into a message.
    `diagnose` can read the tool's own error report from the job folder. Write messages
    with counts, column names and check ids, **never data values** (ADR 0007).
  - `ok_exit_codes`: exit codes that count as success, for check tools that exit 1 when
    they find something.
  - `pool`: jobs in the same pool run one at a time. The launcher sets no pool limits, so
    every pool allows one job, and all tools share the `default` pool unless they name
    their own. Name a pool (e.g. your tool id) if your jobs shouldn't wait behind other
    tools' jobs.
- **What the job manager writes:** `<study>/.opncafyr/jobs/<job id>/`: `state.json`
  (`job-v1`), `events.jsonl`, `stdout.log`, `stderr.log` and, after a failure,
  `diagnostic.json`. Users see the job in the jobs panel, and can cancel it.

Parse a tool's machine-readable result in the adapter (for example deckifyr's
`parse_cli`, quartifyr's `parse_render`), not in the workflow. If the tool has a
descriptor `jobs` contract, read its `timeout_s` from `tool.jobs[capability]`, and pass
`output_check=contract.check_output` with the document's name to have the output checked.
Bundle any schema of your own in `adapters/<id>/schemas/`; never add a tool's schema to
`src/opncafyr/schemas/`.

## 4. Giving users a way in: workflow packs

A descriptor is enough for a tile, but core has no button that starts a web app or a
job: what to start, on which files, belongs to a user journey. That is a **workflow pack**:
a package under `src/opncafyr/workflows/<pack>/` whose `workflow()` returns a
`core.registry.WorkflowPack`. It can add API routes (`mount(app)`, under
`/api/<pack>/`), page assets (`static`) and pages (`views`, shown in the side
navigation). Core finds packs by package scan, as it finds adapters.

A pack that offered the toy to users could look like this (a sketch; nothing ships it):

```python
# src/opncafyr/workflows/echo/__init__.py
import secrets
from pathlib import PurePosixPath

from fastapi import HTTPException
from pydantic import BaseModel

from opncafyr.adapters import example_echo
from opncafyr.core.jobmanager import JobManagerError
from opncafyr.core.registry import WorkflowPack
from opncafyr.core.supervisor import LaunchError


class Summarize(BaseModel):
    source: str  # workspace-relative CSV


def _fail(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status, {"code": code, "message": message})


def _mount(app) -> None:
    @app.post("/api/echo/summarize")
    def summarize(body: Summarize) -> dict:
        if app.state.workspace is None:
            raise _fail(409, "no_workspace", "Open a study folder first.")
        parts = PurePosixPath(body.source).parts
        if not parts or any(p.startswith(".") for p in parts):
            raise _fail(400, "bad_source", "Choose a CSV in the study folder.")
        output = f"echo/{secrets.token_hex(4)}"  # a fresh folder: outputs are never replaced
        try:
            job_id = app.state.jobs.submit(example_echo.summarize_request(body.source, output))
        except JobManagerError as exc:  # e.g. the launcher is stopping
            raise _fail(409, "jobs_unavailable", str(exc)) from None
        return {"job_id": job_id, "output": output}

    @app.post("/api/echo/open")
    def open_app() -> dict:
        try:
            app.state.supervisor.start("example_echo", {})
        except (KeyError, LaunchError, ValueError) as exc:
            raise _fail(503, "app_unavailable", f"The echo app didn't start: {exc}") from None
        return {"route": "/apps/example_echo/"}


def workflow() -> WorkflowPack:
    return WorkflowPack(id="echo", mount=_mount)
```

Pack routes pass through the same security middleware as every other route: a
state-changing request needs the session secret. `resolve_in_root` accepts hidden paths
such as `.opncafyr/…`, so a pack refuses them itself, as above.

Pages (`views`): each is an ES module served from `/static/workflows/<pack>/` that
exports `init(container)`. Import `getJSON`, `post` and `send` from `../../js/api.js` so
state-changing calls carry the session header (see `workflows/nca/web/common.js`). A
view's `icon` must be one of the symbols the home page already has: `home`, `jobs`,
`settings`, `folder`, `info`, `sun`, `doc` or `table`.

Where outputs go: a tool's own results go in a top-level study folder the workflow names
(like `report/` and `deck/`) or in a run under `.opncafyr/`. `OUTPUTS/` is reserved for
published files (docs/publishing.md). New folders need no workspace schema change; a
record that others rely on is written with `core.manifest.atomic_write_json` against a
versioned schema. The NCA pack
(`src/opncafyr/workflows/nca/`) is the full example. Its publishing pages start the deck
editor (`publish/deck.py`) and submit report and deck jobs (`publish/report.py`,
`publish/deck.py`).

## Security rules

These follow from CLAUDE.md and `docs/architecture.md` (Trust boundaries). Reviewers
reject an adapter that breaks one.

- **Executables come from the runtime.** Every `argv[0]` is `runtime.executable(<name>)`
  for a name in the descriptor. Code checks only that it is an absolute path to an
  existing file, so this is a rule reviewers enforce. A workspace file, a request body or
  a `session` value can supply data and options only. It can never name a program, add a command or choose a
  flag that runs code. Run Python with `-I`, and R with `--vanilla` or `--no-init-file`.
- **No shell.** Pass argv lists. Only `core/proctree.py`, `core/picker.py` and
  `installer/proc.py` may import `subprocess` (`tests/test_boundaries.py`).
- **Paths.** Resolve every workspace path with `core.workspace.resolve_in_root`.
- **Loopback only.** Children bind to `127.0.0.1`, require their token, refuse a foreign
  Host and cross-site requests, and set no CORS headers.
- **Child pages are launcher code.** They share the launcher's origin: escape anything
  data-derived, and load no third-party script.
- **No raw values in logs or messages.** No subject IDs or data values in job messages,
  progress lines, child logs, URLs or diagnostics. Counts, column names and check ids are
  fine.
- **Offline.** Nothing may need the network at run time: no CDN assets, `quarto add`,
  package installs or update checks (`docs/offline.md`).
- **No calculations in the launcher.** Science stays in the tool. An adapter translates
  and checks contracts; it doesn't compute results.
- **core/ stays tool-agnostic.** `core/` never imports `adapters/` or `workflows/`
  (`tests/test_boundaries.py`). Don't add your tool's name to anything under `core/`;
  copy `test_core_and_the_workspace_schema_never_name_the_toy` for your id. (Some core
  comments mention the first tools, and `core/proctree.py` keeps an opnca-r variable off
  every child's environment; that is history, not a pattern to follow.)

## Shipping a tool

A tool that ships in releases needs, besides the parts above:

1. its id in `PRODUCT_TOOLS` (and in `PRODUCT_WEB_APPS` if it has a web app) in
   `tests/tool_sets.py`, which the registry tests compare against;
2. its descriptor in `REQUIRED` in `scripts/check_wheel.py`;
3. its pins (see [Which Python, which R](#which-python-which-r)) and its network
   touchpoints in `docs/offline.md`;
4. any upstream changes it needs, filed in its own repository and listed in
   `docs/upstream.md`.

## Keeping a tool out of releases

A tool that is only for development or tests can still live in `src/opncafyr/adapters/`,
where the registry finds it in a checkout. To keep it out of the release wheel:

1. Add its folder to `exclude` under `[tool.hatch.build.targets.wheel]` in
   `pyproject.toml`.
2. Add it to `FORBIDDEN` in `scripts/check_wheel.py`. `scripts/make_installer.py` runs that
   check on every wheel it builds, and CI builds a wheel and checks it.
3. Add its id to `DEV_ONLY` in `tests/tool_sets.py`.

A test-only tool that needs no web app can instead live under `tests/`, loaded with
`load_descriptors(root)`, like `tests/toy_adapters/toy_notes/`.

## Known limits

- **No WebSockets** through the proxy (see [3a](#3a-a-web-app)).
- **New executables need installer work.** The symbolic names come from `runtime.json`,
  which the installer writes (`installer/steps/runtimes.py`). A tool that needs a new
  runtime (a new binary, a new Python environment) also needs pins in
  `runtime/pins.toml`, an install step and an offline bundle entry.
- **The job manager checks one output document.** `output_document` names a single
  JSON file; check anything else (several files, tables, figures) in `review`.

## Testing checklist

- [ ] The descriptor loads: `load_descriptors()` lists the tool, and an invalid copy fails
      with the file and field named (`tests/test_registry_schemas.py` has the patterns).
- [ ] Health: `ok` with the runtime, `unavailable` with a clear reason without it
      (`tests/test_tool_health.py`).
- [ ] The tile: `GET /api/tools` shows the display name and health.
- [ ] Web app: started through `core.app.build_supervisor`, it shows `running` in
      `GET /api/apps`, its page and API work through `/apps/<id>/`, and the child refuses
      API calls without its token. See `tests/test_example_echo.py`.
- [ ] If the real tool runs under a prefix, it passes the base-path check (#16).
- [ ] Jobs: completed with good input; failed with a readable message, and no output
      moved into place, with bad input or a path outside the study; cancelled cleanly; a
      second run with the same output folder is refused, so the workflow uses fresh ones.
- [ ] The child refuses requests without its token, with a foreign Host and cross-site
      (`Sec-Fetch-Site: cross-site`).
- [ ] No data values in messages or written summaries (a fixture subject ID or
      concentration never appears).
- [ ] `core/` never names the tool, and `tests/test_boundaries.py` and
      `tests/test_home_static.py` still pass.
- [ ] A tool with a fake executable for CI, like `tests/fake_deckifyr.py`, if the real one
      is slow or needs a licence.
- [ ] Offline: the tool starts and its jobs run with the network cut (`docs/offline.md`).
- [ ] Docs: `docs/architecture.md`, CHANGELOG, and the tool's pins and upstream issues
      (`docs/upstream.md`).
