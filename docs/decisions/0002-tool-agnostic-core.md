# 0002. Tool-agnostic core with trusted adapters

- Status: accepted
- Date: 2026-11-04

## Context
The first release targets opnca-r with deckifyr and quartifyr, but the same launcher could
host other analysis or publishing tools later.

## Decision
`opncafyr.core` knows only capabilities and file contracts. Each tool is an installed,
versioned descriptor plus an adapter implementing `prepare/validate/run/status/cancel/
artifacts/shutdown`. The clin pharm journey lives in a workflow pack
(`opncafyr.workflows.nca`). Workspace files can never register tools or name executables.

## Consequences
- `core/` must not import adapters or workflows; reviewers should reject PRs that do.
- Adding a tool means a descriptor, a health check and an adapter; no core changes.
- Gate 4 includes a toy second adapter to prove this boundary holds.
