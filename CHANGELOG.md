# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/). `VERSION` is the source of truth.

## [Unreleased]

### Added
- Proxy compatibility harness (#16) in `tests/integration/proxy/`, run with
  `pytest -m integration`. It runs opnca-r 0.2.7 and deckifyr 0.3.5 from their pinned
  releases behind the proxy and checks:
  - that every page request stays under the prefix (Playwright);
  - an API round trip for each app: opnca-r schema plus calculate, and deckifyr save,
    build and PPTX download;
  - the attacks reproduced in the upstream reviews, both through the proxy and straight to
    the child port;
  - that an orderly stop and a tree kill both leave no processes behind.

  Results are in `docs/spikes/basepath.md`. CI runs the harness in a new macOS 15 job.
  The opnca-r half needs the `OPNCA_R_RELEASE_TOKEN` secret and is skipped with a
  warning without it.
- `opncafyr.adapters.opnca.release` selects an opnca-r asset from `manifest.json` by its
  fields and verifies it against `runtime/pins.toml`, with no network. #34 reuses it.
- Reverse proxy for child apps under `/apps/<tool>/` (#15).
  - Request and response bodies are streamed, with a per-descriptor body limit (413).
  - `/apps/<tool>` redirects to `/apps/<tool>/` with a 308.
  - Before forwarding, the proxy rewrites `Host` to the child's port, drops `Origin`,
    `Cookie` and the session header, and injects the child's token. It strips
    `Set-Cookie` and CORS headers from responses.
  - A crashed child gets a 502 page with a restart button.
  - `GET /api/apps` reports each app's state.
  - The home page sets a `HttpOnly; SameSite=Strict; Path=/apps/` session cookie. It is
    required on every proxied request, along with a same-origin `Sec-Fetch-Site` and our
    own `Origin` on state-changing requests.
- opnca and deckifyr web adapters (start, ready, stop):
  - opnca-r 0.2.7 runs `scripts/serve.R` with `--base-path`, the API and shutdown tokens,
    `--lock-dataset`, `--no-overwrite` and `--require-blq-rules`;
  - deckifyr 0.3.5 runs `serve` with `--root-path`, its API token, a fixed port and
    `--trusted-binaries-only`.
- Child process supervision (#12, #9). `core/proctree.py` spawns argv arrays only, with an
  allow-listed environment (it always drops `OPNCAFYR_*` and the variables that widen
  opnca-r's Host check), and kills whole process trees: freeze-and-walk on POSIX, a Job
  Object on Windows. `core/supervisor.py` starts one child per tool:
  - with a per-child token, a fixed loopback port (retried after a port race) and a
    launcher-owned cwd and TMPDIR;
  - waits for the adapter's readiness probe;
  - stops through the adapter's orderly stop, then a tree kill.
- `core/runtime.py` and `schemas/runtime-v1.schema.json`: the installed runtime record that
  names the absolute paths of the pinned executables.
- Descriptors can set `web_app.max_body_bytes`. The registry finds web-app adapters by
  package scan, and a test checks that `core/` never imports adapters or workflows.
- Project scaffold: loopback FastAPI controller with `/health`, Host/Origin and
  session-secret checks, workspace path canonicalization, job state machine, trusted tool
  descriptor registry, JSON Schemas (tool descriptor, workspace, run manifest,
  publication, recipe), adapter stubs for opnca, deckifyr and quartifyr, installer stubs,
  CI, and the plan of action.

### Changed
- Recorded the v1 platform matrix (ADR 0003, #1): Windows 11 x86_64, macOS arm64/x86_64
  (macOS 15 until opnca-r#45), R 4.6.2, a 30-minute configurable idle timeout and no
  native Linux. Also recorded the opnca-r install path (ADR 0004, #4): prebuilt binaries,
  with the vendored source tarball as the repair/exception fallback.
  `runtime/pins.toml` now has the platform table and the R pin.
- Reviewed the merged opnca-r (#23-#30, v0.2.6) and quartifyr (#69-#72) work against the
  issues we filed. `docs/upstream.md` now has a status column and the follow-ups
  (opnca-r #40-#43, quartifyr #75-#78). `docs/plan.md` risks are updated, and
  `runtime/pins.toml` pins opnca-r 0.2.6 assets and the quartifyr commit and tarball.
- Reviewed deckifyr 0.3.4 (#82-#86) and filed follow-ups #88-#91. `runtime/pins.toml` now
  pins the deckifyr 0.3.4 wheel, sdist and R tarball by SHA-256.
- Reviewed quartifyr 0.3.2 (#75-#78) and filed follow-ups #81-#82. `runtime/pins.toml`
  now pins the quartifyr 0.3.2 tag commit, tarball and `SHA256SUMS` by SHA-256.
- Reviewed deckifyr 0.3.5 (#88-#91) and pinned its wheel, sdist and R tarball by SHA-256.
  `upstream-blocked` removed from #33.
- Reviewed opnca-r 0.2.7 (#40-#43) and filed follow-ups #45-#46. `runtime/pins.toml` now
  pins the 0.2.7 binaries, vendored tarball, `manifest.json` and `SHA256SUMS` by SHA-256.
  `upstream-blocked` removed from #27.
