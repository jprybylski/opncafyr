# 0002. Tool-agnostic core with trusted adapters

- Status: accepted
- Date: 2026-11-04

## Context
The first release targets opnca-r with deckifyr and quartifyr, but the same launcher could
host other analysis or publishing tools later.

## Decision
`opncafyr.core` knows only capabilities and file contracts. Each tool is an installed,
versioned descriptor plus an adapter. The adapter supplies a web app
(`core.adapter.WebAppAdapter`) and/or job commands (`core.jobmanager.CommandSpec`) that a
workflow pack submits as `JobRequest`s; the job manager owns run, status, cancel and
outputs. (The decision first named a seven-method adapter, `prepare/validate/run/status/
cancel/artifacts/shutdown`; see the notes below.) The clin pharm journey lives in a
workflow pack (`opncafyr.workflows.nca`). Workspace files can never register tools or
name executables.

## Consequences
- `core/` must not import adapters or workflows; reviewers should reject PRs that do.
- Adding a tool means a descriptor, a health check and an adapter; no core changes.
- Gate 4 includes a toy second adapter to prove this boundary holds.

## Note (2026-11-07, #41)
The contract a tool implements turned out to be narrower than the seven methods above. A
web app's adapter implements `core.adapter.WebAppAdapter` (`launch`, `is_ready`, `stop`,
optional `activity`). A job is a `CommandSpec` from the adapter, which a workflow pack
submits to the job manager as a `JobRequest`, so the job manager owns run, status, cancel
and outputs. The `core.adapter.Adapter` Protocol is not used by any adapter; removing it
is a follow-up. The toy adapter `adapters/example_echo/` shows that a tool can be added
without core changes, and [adding-a-tool.md](../adding-a-tool.md) describes the contract.

## Note (2026-11-07, #55)
The unused `core.adapter.Adapter` Protocol and its `JobHandle`, `ValidationResult` and
`Artifact` types were removed. A descriptor's `jobs` contract may now name a schema
bundled in the adapter's own `schemas/` folder as well as a launcher schema (a name in
both is refused), and a `JobRequest` may ask the job manager to check one output document
against the contract's output schema before the job completes. The toy declares
`jobs.summarize_csv` with its bundled `summary-v1` schema.
