# Upstream issues

Changes opncafyr needs in the tools it drives. Each was filed in the tool's own repo on
2026-11-04. Update the status column as they land; the opncafyr issues that depend on them
carry the `upstream-blocked` label.

## mantra-opnca/opnca-r (private)

| Issue | What | Needed by |
|---|---|---|
| [#24](https://github.com/mantra-opnca/opnca-r/issues/24) (existing; [launcher comment](https://github.com/mantra-opnca/opnca-r/issues/24#issuecomment-6648467601)) | Work behind a reverse proxy: configurable base path and allowed authorities, relative API paths | Gate 1 proxy |
| [#23](https://github.com/mantra-opnca/opnca-r/issues/23) (existing; [launcher comment](https://github.com/mantra-opnca/opnca-r/issues/23#issuecomment-6648467843)) | Vendored source tarball for offline builds, with checksums | Gate 0 fallback, Gate 4 offline |
| [#25](https://github.com/mantra-opnca/opnca-r/issues/25) | File-backed, foreground launcher entry point (`opnca_serve()`) | Gate 2 opnca adapter |
| [#26](https://github.com/mantra-opnca/opnca-r/issues/26) | Atomic server-side run export to an approved directory | Gate 2 durable save |
| [#27](https://github.com/mantra-opnca/opnca-r/issues/27) | Generated R code reads the real relative input path | Gate 2 replayable script |
| [#28](https://github.com/mantra-opnca/opnca-r/issues/28) | Platform R binaries + SHA-256 manifest + build diagnostics | Gate 4 (target for first release) |
| [#29](https://github.com/mantra-opnca/opnca-r/issues/29) | App maps LLOQ, BLQ, missing, nominal time, tau, infusion duration | Gate 2 mapping hand-off |
| [#30](https://github.com/mantra-opnca/opnca-r/issues/30) | Scope or disable persisted localStorage under a shared origin | Gate 2 multi-analyte safety |

Finding: the spec says opnca-r releases include `vendor.tar.xz`. The v0.2.4 release
tarball does not, so a source install currently needs network access to crates.io as
well as a Rust toolchain. #23 addresses this.

## jprybylski/deckifyr (public)

| Issue | What | Needed by |
|---|---|---|
| [#82](https://github.com/jprybylski/deckifyr/issues/82) | Serve the web editor under a path prefix | Gate 1 proxy / Gate 3 |
| [#83](https://github.com/jprybylski/deckifyr/issues/83) | `serve` launcher mode: dirty state on `/api/health`, orderly stop | Gate 1 lifecycle / Gate 3 |
| [#84](https://github.com/jprybylski/deckifyr/issues/84) | Job timeouts, cancellation, isolated LibreOffice profile | Gate 3 |
| [#85](https://github.com/jprybylski/deckifyr/issues/85) | Versioned `--json` result schema with full warnings | Gate 3 |
| [#86](https://github.com/jprybylski/deckifyr/issues/86) | Versioned Python wheel with SHA-256 | Gate 0 pins |

## jprybylski/quartifyr (public)

| Issue | What | Needed by |
|---|---|---|
| [#69](https://github.com/jprybylski/quartifyr/issues/69) | Noninteractive job contract; fail loudly on partial renders | Gate 3 |
| [#70](https://github.com/jprybylski/quartifyr/issues/70) | `check_report_project()` render-free preflight | Gate 3 |
| [#71](https://github.com/jprybylski/quartifyr/issues/71) | Quarto render timeout and process-tree cleanup | Gate 3 |

## Not filed

- **mantra-opnca/opnca (core).** No changes needed: the launcher filters to one analyte
  before opnca-r sees the data, so the core's deferred multi-analyte axis is not required.
- **A2-ai/reportifyr.** External. Sidecar validation lives in the opncafyr publishing
  adapter instead of a reportifyr change.
