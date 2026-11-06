# CLAUDE.md

Guidance for coding agents working in this repository.

## What this is

opncafyr is a **local, per-user workspace launcher**. An installer (no admin rights) pins
uv/Python, R, Quarto and the tool packages under the user's app-data folder. The launcher
then starts one FastAPI controller on `127.0.0.1:<ephemeral port>` and opens the browser.
From there a user picks a study folder, imports and cleans a CSV, maps columns, selects
**exactly one analyte**, and runs it in opnca-r. Each run is a replayable, hashed bundle.
Completed runs can be published to a DOCX report (quartifyr + reportifyr) or a PPTX deck
(deckifyr).

- Spec: `docs/spec.md` (the source of design intent; cite sections in issues and PRs).
- Plan and gates: `docs/plan.md`. Upstream issue index: `docs/upstream.md`.
- Layering and trust boundaries: `docs/architecture.md`. Decisions: `docs/decisions/`.

**Research use only.** Never add wording, UI or docs that claim clinical or regulatory
validation. An end-to-end DOCX/PPTX success proves artifact assembly, not science.

## Layout

```
src/opncafyr/
  core/          tool-agnostic framework: app, security, workspace paths, jobs, registry,
                 manifests, proxy, single-instance. MUST NOT import adapters/ or workflows/.
  adapters/<t>/  one trusted adapter per tool + descriptor.yaml (opnca, deckifyr, quartifyr)
  workflows/nca/ the clin pharm flow (roles, analyte filter) built on core + adapters
  schemas/       JSON Schemas (*-v1.schema.json); versioned, never edited incompatibly
  r/             fixed R programs run by the launcher
  web/static/    home page assets
installer/       install.sh (macOS), install.bat -> install.ps1 (Windows)
runtime/         pins.toml: exact runtime/package versions and checksums
  r/             renv.lock + binaries.toml: the app-private R library, per platform
  python/tools/  locked uv project for the tools' Python (deckifyr, quartifyr, reportifyr)
scripts/         pin generators and verify_pins.py (dev/CI tooling, not shipped)
tests/           pytest; fixtures/ are generated, never hand-edited
```

## Commands

```sh
uv sync                       # dev environment (Python pinned in .python-version)
uv run pytest                 # all tests
uv run ruff check             # lint (must pass)
uv run ruff format            # format (CI runs --check)
uv run opncafyr launch        # start controller; --no-browser --port N for testing
uv run opncafyr doctor        # paths and tool availability
uv run python scripts/verify_pins.py [--network]   # pins and locks (docs/dependency-graph.md)
```

Run `ruff check`, `ruff format --check` and `pytest` before every commit.

## Invariants (do not break; add a test when you touch them)

**Reusable core.** The controller knows capabilities and file contracts, not tool screens
or calculations. A new tool must be addable with a descriptor, a health check and an
adapter without changing core navigation or the workspace schema. If a change needs
tool-specific logic in `core/`, it belongs in an adapter or workflow instead.

**Security.** The local URL is a security boundary.
- Bind only to `127.0.0.1`; children bind to their own loopback ports.
- Every request passes the Host check; state-changing requests need the per-launch
  session secret and a same-origin `Origin` if present. Never add CORS headers.
- Resolve every workspace path with `core.workspace.resolve_in_root`.
- Subprocesses take argv arrays (`shell=False`), executables come from trusted installed
  descriptors and the pinned runtime. Workspace files supply data and options only; they
  can never name an executable or add a command.
- No raw subject/patient values in logs, URLs, telemetry or diagnostic bundles. No dataset
  leaves the machine by default.

**Science.** opnca-r is the calculation authority; never implement NCA here.
- One analyte per run, filtered by the exact raw value of the analyte column.
- Never rewrite source files, silently drop rows, set BLQ to zero, substitute nominal for
  actual time, infer units, or resolve duplicates automatically. Every cleaning step shows
  before/after row counts and keeps a rejected-row log.
- When a choice is ambiguous, ask the user (a `needs_choice`-style prompt); do not default.
- Completed runs are immutable; any change to cleaning, mapping, exclusions or rules
  creates a new run. Only `completed` runs may be published.
- Publication and build manifests pin exact run IDs and artifact SHA-256s, never "latest".

**Offline-capable (supported v1 use case).** Most installs are online: the installer
downloads pinned, checksummed assets. Firewalled environments (such as GxP cloud
analytical systems) are common, though, so offline has to work too.
- Install works both online and from a verified bundle or internal mirrors (#36), through
  one code path.
- Every downloaded asset is pinned with a SHA-256 in `runtime/pins.toml`.
- Nothing at runtime may *require* the network. Don't add CDN assets or web fonts,
  `quarto add`, `uv lock` refreshes or `curl` installers. Update checks and telemetry are
  off by default.
- In offline mode, configure the tools so they never reach out (`UV_OFFLINE`,
  `UV_NO_INDEX`, `UV_FIND_LINKS`, `UV_PYTHON_DOWNLOADS=never`, local renv repositories,
  bundled Quarto extensions).
- A step that needs the network and has no offline equivalent is a bug. Add it to #44
  with a resolution.
- Every known network touchpoint and its resolution is listed in `docs/offline.md`; the
  `offline` CI job installs and runs with the network cut and fails on any attempt.

**Files.** Manifests and workspace JSON are schema-validated and written with
`core.manifest.atomic_write_json`. Incomplete output directories must never look complete.
The workspace metadata folder is `.opncafyr/` and its schema is versioned with migrations.

## Upstream tools

| Tool | Repo | Local checkout | Notes |
|---|---|---|---|
| opnca-r | mantra-opnca/opnca-r (private) | `~/opnca/bindings/r` (submodule of mantra-opnca/opnca) | plumber2 app; Rust source build |
| opnca core | mantra-opnca/opnca (private) | `~/opnca` | Rust engine; usually no changes needed |
| deckifyr | jprybylski/deckifyr (public) | `~/deckifyr` | FastAPI + React editor, Python CLI |
| quartifyr | jprybylski/quartifyr (public) | `~/quartifyr` | R; drives Quarto + reportifyr |
| reportifyr | A2-ai/reportifyr | — | **external**: do not file issues; adapt in our adapter |

When a tool needs to change, **file an upstream issue** (Goal / Current behavior with file
links / Proposed implementation / Acceptance criteria) and record it in `docs/upstream.md`.
A temporary workaround in an adapter is allowed only if it is tested and labelled
temporary. Never scrape browser downloads from opnca-r. Public repos (deckifyr, quartifyr)
must not receive details of private repos or any data.

## Reusing makecoolplots (`~/makecoolplots`, Rust)

Port ideas into Python/R; do not link to it. Useful sources:
- Column-role aliases and value checks: `crates/mcp-inspect/src/roles.rs`
- Routes, units, BLQ parsing: `crates/mcp-inspect/src/nca_input.rs` (but do **not** port
  its silent dropping of undosed subjects)
- Analyte choice + ask-don't-guess: `crates/mcp-codegen/src/endpoint.rs`,
  `crates/mcp-core/src/error.rs` (`needs_choice`)
- Recipe shape: `crates/mcp-codegen/src/clean.rs` (`LoadCleanOptions`)
- Fixed-reader Rscript pattern: `docs/architecture.md` there (`--no-init-file`, env args)
- QC checklist: `kb/topics/data-checking.md`
- Fixtures: `fixtures/messy_generic.csv`, `adpc*.csv`, `sparse_*.csv` (copy with their
  generator; they have no multi-analyte PK case, so we generate our own)

## Workflow

- One issue per branch: `feat/<issue>-<slug>`, `fix/<issue>-<slug>`, `docs/<slug>`.
  Merge via PR; reference `#<issue>` in commits and the PR.
- Add a `CHANGELOG.md` entry under `[Unreleased]` (Keep a Changelog).
- `VERSION` is the single version source; keep `pyproject.toml` in sync.
- Issues are grouped by milestones `Gate 0`–`Gate 4` and `area:*` / `adapter:*` labels.
  `decision-needed` issues block implementation that depends on them; don't guess the
  answer in code.
- Fixtures are produced by seeded generator scripts committed alongside them.
