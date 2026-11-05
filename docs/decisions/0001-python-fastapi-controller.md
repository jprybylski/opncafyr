# 0001. Python + FastAPI loopback controller, browser UI

- Status: accepted (from docs/spec.md, 3 Nov 2026)
- Date: 2026-11-04

## Context
The product needs one persistent local process that supervises R and Python child apps,
runs jobs, and serves a UI to non-technical users on Windows and macOS without admin rights.

## Decision
Use a Python application in its own uv environment, with FastAPI/Starlette as the
controller, bound to `127.0.0.1` on an ephemeral port. The UI is the user's default
browser. All child apps are served beneath the same origin.

## Consequences
- Reuses the Python runtime already required by deckifyr.
- Browser page-unload is unreliable, so shutdown uses heartbeats, an explicit Quit action
  and an idle timeout instead.
- Child apps must work behind a path prefix (upstream issues opnca-r #24, deckifyr #82).
- A native window (e.g. Tauri with sidecars) remains a later option.
