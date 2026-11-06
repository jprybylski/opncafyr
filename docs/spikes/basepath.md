# Spike #16: child apps behind `/apps/<tool>/`

- Date: 2026-11-06
- Tools: opnca-r 0.2.7 (macOS arm64 binary, R 4.6) and deckifyr 0.3.5 (wheel), both
  verified against `runtime/pins.toml`
- Harness: `tests/integration/proxy/`, run with `pytest -m integration`. CI runs it in the
  `integration-proxy` job (macOS 15 arm64).

**Result:** both apps work behind the launcher proxy with **no rewriting**. Each child is
started with its own base path (opnca-r `--base-path /apps/opnca/`, deckifyr `--root-path
/apps/deckifyr`). The proxy forwards the full path unchanged, rewrites `Host` to the
child's port, drops `Origin`, `Referer` and `Cookie`, and injects the child's API token.
Neither child needs `--allowed-host`. No temporary HTML or JS rewrite is needed, so the
spec's interim-rewrite question doesn't come up.

## Results

Local run (macOS 26.6 arm64, R 4.6.1 with the R 4.6.2-built binary): 11 of 11 passed.
The CI job runs the same checks on macOS 15. Its opnca-r half needs the
`OPNCA_R_RELEASE_TOKEN` secret.

| Check | opnca-r 0.2.7 | deckifyr 0.3.5 | Workaround |
|---|---|---|---|
| Page and all static assets load under the prefix (Playwright, every response < 400) | Works (34 requests: 27 assets, a font, `api/v1/meta`, `dataset/preview`, `exclusions`, `qc`) | Works (12 requests: assets, logo, `api/health`, `project`, `config/*`, `plan`, `furniture`, `layouts`) | None |
| No request escapes `/apps/<tool>/` | Works | Works | None |
| `/apps/<tool>` → `/apps/<tool>/` | The proxy answers with a 308 (unit-tested) | The proxy answers with a 308 (unit-tested) | None (the proxy owns it) |
| API round trip | Works: `dataset/schema`, then `analysis/calculate` with a full preset (all roles, `""` for unmapped optional roles, `blq_method`, all three BLQ rules) | Works: `save`, then `build`, poll the job, download the PPTX (starts with `PK`) | None |
| `--require-blq-rules` enforced through the proxy | Works (400 without the rules) | n/a | None |
| Proxy without the session cookie | 403 | 403 | None |
| Proxy with a cross-site or same-site `Sec-Fetch-Site` | 403 | 403 | None |
| Proxy POST with a foreign `Origin` | 403 | 403 | None |
| Child port: cross-site form POST, no token | 403 | 403 | None (opnca-r#40, deckifyr#88) |
| Child port: foreign Host with a valid token | 400 | 421 | None |
| Child port: no token | 403 | 403 | None |
| Orderly stop | Works: `POST api/v1/shutdown` with both tokens, exit 0, no descendants left, workdir removed | Works: `POST api/shutdown` (`if_dirty: save`), exit 0, no descendants left, workdir removed | None |
| Tree kill without an orderly stop | No processes left | No processes left | None |

## How to reproduce

```sh
S=$(mktemp -d)
mkdir -p "$S/dl/opnca" "$S/dl/deckifyr"
gh release download v0.2.7 -R mantra-opnca/opnca-r -p manifest.json -D "$S/dl/opnca"
asset=$(uv run python -m opncafyr.adapters.opnca.release select \
  --pins runtime/pins.toml --manifest "$S/dl/opnca/manifest.json")
gh release download v0.2.7 -R mantra-opnca/opnca-r -p "$asset" -D "$S/dl/opnca"
gh release download v0.3.5 -R jprybylski/deckifyr \
  -p deckifyr-0.3.5-py3-none-any.whl -D "$S/dl/deckifyr"
rt=$(uv run python tests/integration/proxy/prepare_runtime.py --out "$S/rt" \
  --opnca-dir "$S/dl/opnca" --deckifyr-wheel "$S/dl/deckifyr/deckifyr-0.3.5-py3-none-any.whl")
uv run --group integration playwright install chromium
OPNCAFYR_RUNTIME_FILE="$rt" uv run --group integration pytest -m integration tests/integration/proxy
```

opnca-r's server packages (plumber2, httpuv, reqres, jsonlite, processx, R6) have to be
visible to R. `prepare_runtime.py` installs opnca-r into a private library, which the
adapter passes as `R_LIBS`. R's system library is still searched. CI installs those
packages into the private library first.

## Notes and limits

- **opnca-r static files have no guard on the child port.** `index.html` and the assets
  are served without a Host, token or cross-site check; only the API routes are guarded.
  The static files hold no data, so this is acceptable, but a page on another origin can
  still fetch them from the child port.
- **Shared origin.** Child pages run on the launcher's origin, so a child's own scripts
  could read the home page (and the session secret in it). The children are trusted,
  pinned code. The boundary the proxy enforces is against *other* origins and other
  local ports.
- **Not covered yet:** the routes upstream's own proxy test misses (exclude, terminal,
  morphometry, dose proportionality, job cancel, `export/save`). They belong to the opnca
  adapter work (#27, #28), which owns those flows. A tree kill while an opnca-r job worker
  is running is covered only by the toy-child unit test, which uses a `setsid`
  grandchild.
- **Windows:** the core process-tree code (Job Object) runs in the Windows unit-test job.
  The harness itself is macOS-only until #6.
