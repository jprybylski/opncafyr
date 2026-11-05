# Upstream issues

Changes opncafyr needs in the tools it drives. The first round was filed in each tool's repo
on 2026-11-04. Merged work was reviewed against those issues on 2026-11-05, and the gaps
found were filed as follow-ups. The opncafyr issues that depend on them carry the
`upstream-blocked` label, and adapter-side requirements from the review are recorded as
comments on #15/#16, #27, #28, #32, #33 and #34.

## mantra-opnca/opnca-r (private)

| Issue | What | Status | Needed by |
|---|---|---|---|
| [#24](https://github.com/mantra-opnca/opnca-r/issues/24) | Work behind a reverse proxy: base path, allowed authorities, relative API paths | Done (PR #38, v0.2.5) | Gate 1 proxy |
| [#23](https://github.com/mantra-opnca/opnca-r/issues/23) | Vendored source tarball for offline builds, with checksums | Done (PR #39, v0.2.6); some checks still unverified, see #43 | Offline install (#36) |
| [#25](https://github.com/mantra-opnca/opnca-r/issues/25) | File-backed, foreground launcher entry point (`opnca_serve()`, `scripts/serve.R`) | Done (PR #38) | Gate 2 opnca adapter |
| [#26](https://github.com/mantra-opnca/opnca-r/issues/26) | Atomic server-side run export to an approved directory | Done (PR #38) | Gate 2 durable save |
| [#27](https://github.com/mantra-opnca/opnca-r/issues/27) | Generated R code reads the real relative input path | Done (PR #38) | Gate 2 replayable script |
| [#28](https://github.com/mantra-opnca/opnca-r/issues/28) | Platform R binaries + SHA-256 manifest + build diagnostics | Done (PR #39, v0.2.6); clean-install and cellar checks still open, see #43 | Gate 4 |
| [#29](https://github.com/mantra-opnca/opnca-r/issues/29) | App maps LLOQ, BLQ, missing, nominal time, tau, infusion duration | Done (PR #38) | Gate 2 mapping hand-off |
| [#30](https://github.com/mantra-opnca/opnca-r/issues/30) | Scope or disable persisted localStorage under a shared origin | Done (PR #38) | Gate 2 multi-analyte safety |
| [#40](https://github.com/mantra-opnca/opnca-r/issues/40) | **Security:** launcher API token; reject cross-site requests (a cross-site form POST can change exclusions today; reproduced) | Open, blocks opncafyr #27 | Gate 2 |
| [#41](https://github.com/mantra-opnca/opnca-r/issues/41) | `--no-overwrite`: a saved run can't be replaced | Open; adapter blocks `overwrite:true` meanwhile (#28) | Gate 2 |
| [#42](https://github.com/mantra-opnca/opnca-r/issues/42) | `--require-blq-rules`, preset `blq_method`, no nominal-time guess for `time`, BLQ flag types | Open; adapter sends a full preset meanwhile (#27) | Gate 2 |
| [#43](https://github.com/mantra-opnca/opnca-r/issues/43) | Verify clean installs, renv cellar, MSRV 1.87, Windows shutdown; scalable asset names; manifest `schema_version` | Open; installer selects by manifest meanwhile (#34) | Gate 0/4 |

Select v0.2.6 assets by `manifest.json` fields, never by file name. The Windows binary is
`opnca_<ver>.zip`, while macOS binaries are `opnca_<ver>_R<minor>_macos-<arch>.tgz`.

## jprybylski/deckifyr (public)

All five were released in v0.3.4 (PR #87, 2026-11-05) and reviewed. The follow-ups
#88–#91 cover the gaps.

| Issue | What | Status | Needed by |
|---|---|---|---|
| [#82](https://github.com/jprybylski/deckifyr/issues/82) | Serve the web editor under a path prefix | Done (v0.3.4) | Gate 1 proxy / Gate 3 |
| [#83](https://github.com/jprybylski/deckifyr/issues/83) | `serve` launcher mode: dirty state on `/api/health`, orderly stop | Done (v0.3.4); SIGTERM still loses unsaved edits, so use `POST /api/shutdown` | Gate 1 lifecycle / Gate 3 |
| [#84](https://github.com/jprybylski/deckifyr/issues/84) | Job timeouts, cancellation, isolated LibreOffice profile | Done (v0.3.4); Windows tree kill untested (#91) | Gate 3 |
| [#85](https://github.com/jprybylski/deckifyr/issues/85) | Versioned `--json` result schema with full warnings | Done (v0.3.4); unexpected errors print no JSON (#90) | Gate 3 |
| [#86](https://github.com/jprybylski/deckifyr/issues/86) | Versioned Python wheel with SHA-256 | Done (v0.3.4); the release can be re-uploaded under the same version (#91) | Gate 0 pins; offline wheelhouse (#36) |
| [#88](https://github.com/jprybylski/deckifyr/issues/88) | **Security:** Host allow-list, cross-site rejection, launcher token (a cross-site form POST built the deck; reproduced) | Open, blocks opncafyr #33 | Gate 3 |
| [#89](https://github.com/jprybylski/deckifyr/issues/89) | Forbid project-file executables (`binary` fields) and paths outside the project | Open; adapter rejects them meanwhile (#33) | Gate 3 |
| [#90](https://github.com/jprybylski/deckifyr/issues/90) | Atomic PPTX, previews and manifest; JSON for every `--json` failure | Open; adapter builds in staging meanwhile (#33) | Gate 3 |
| [#91](https://github.com/jprybylski/deckifyr/issues/91) | Releases can't change after publishing; small contract gaps | Open; pin by SHA-256 and mirror the wheel | Gate 0 pins |

## jprybylski/quartifyr (public)

| Issue | What | Status | Needed by |
|---|---|---|---|
| [#69](https://github.com/jprybylski/quartifyr/issues/69) | Noninteractive job contract; fail loudly on partial renders | Done (PR #74); exits non-zero only with `--strict` | Gate 3 |
| [#70](https://github.com/jprybylski/quartifyr/issues/70) | `check_report_project()` render-free preflight | Done (PR #74); no script entry point, see #78 | Gate 3 |
| [#71](https://github.com/jprybylski/quartifyr/issues/71) | Quarto render timeout and process-tree cleanup | Done (PR #74); `quarto render` only; Windows untested | Gate 3 |
| [#72](https://github.com/jprybylski/quartifyr/issues/72) | Offline / pre-provisioned project setup | Done for init (PR #74); render, see #76 | Gate 0 offline audit, Gate 3 |
| [#75](https://github.com/jprybylski/quartifyr/issues/75) | Release #69–#72 as a new version (the v0.3.1 tag and tarball were replaced by the release workflow's refresh step, as in deckifyr#91) | Open; we pin the commit and tarball SHA-256 meanwhile | Gate 0 pins |
| [#76](https://github.com/jprybylski/quartifyr/issues/76) | Offline-safe render (no venv re-sync or index access) | Open; adapter sets `UV_*` env meanwhile (#32) | Gate 3, offline |
| [#77](https://github.com/jprybylski/quartifyr/issues/77) | A failed render leaves the previous draft/final looking complete | Open; adapter renders in staging meanwhile (#32) | Gate 3 |
| [#78](https://github.com/jprybylski/quartifyr/issues/78) | Result `schema_version`, input hashes, tool versions; `check.R`; small fixes | Open; adapter hashes inputs itself meanwhile (#32) | Gate 3 |

Offline installation is a supported v1 use case (2026-11-04). pyro and reportifyr (A2-ai,
external) also reach the network during project setup. opncafyr handles that in its
adapter (#44) rather than through upstream issues.

## Not filed

- **mantra-opnca/opnca (core).** No changes needed: the launcher filters to one analyte
  before opnca-r sees the data, so the core's deferred multi-analyte axis is not required.
- **A2-ai/reportifyr.** External. Sidecar validation lives in the opncafyr publishing
  adapter instead of a reportifyr change.
