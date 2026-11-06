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
| [#40](https://github.com/mantra-opnca/opnca-r/issues/40) | **Security:** launcher API token; reject cross-site requests | Done (v0.2.7); verified against a running server | Gate 2 |
| [#41](https://github.com/mantra-opnca/opnca-r/issues/41) | `--no-overwrite`: a saved run can't be replaced | Done (v0.2.7); no lock across processes (#46) | Gate 2 |
| [#42](https://github.com/mantra-opnca/opnca-r/issues/42) | `--require-blq-rules`, preset `blq_method`, no nominal-time guess for `time`, BLQ flag types | Done (v0.2.7); `blq_method` still defaults and roles are still guessed when not preset (#46) | Gate 2 |
| [#43](https://github.com/mantra-opnca/opnca-r/issues/43) | Verify clean installs, renv cellar, MSRV 1.87, Windows shutdown; scalable asset names; manifest `schema_version` | Done (v0.2.7) except clean-account installs and a CI cellar restore, which our spikes cover (#6, #7, #34) | Gate 0/4 |
| [#45](https://github.com/mantra-opnca/opnca-r/issues/45) | macOS binaries require macOS 15 (no `MACOSX_DEPLOYMENT_TARGET`); match R 4.6's macOS 13 | Open; sets our macOS minimum to 15 meanwhile (#1) | Gate 0 |
| [#46](https://github.com/mantra-opnca/opnca-r/issues/46) | Save lock across processes; `blq_method` and role-guess defaults; record every effective `opnca()` setting | Open; adapter uses one folder per session, sends a full preset and records the opnca-r version (#27, #28) | Gate 2 |
| [#47](https://github.com/mantra-opnca/opnca-r/issues/47) | `POST /api/v1/shutdown` reply sometimes cut off before the server stops | Open; adapter treats a 202 status line followed by process exit as a clean stop (#27) | Gate 2 |

Select assets by `manifest.json` fields, never by file name. Since v0.2.7 every binary is
`opnca_<ver>_R<minor>_<platform>-<arch>.<ext>`. Rename it to `opnca_<ver>.<ext>` before
`R CMD INSTALL` or a renv cellar restore.

## jprybylski/deckifyr (public)

#82–#86 were released in v0.3.4 (PR #87) and #88–#91 in v0.3.5 (PR #92, 2026-11-06). Both
were reviewed. Remaining gaps are handled in our adapter (#33).

| Issue | What | Status | Needed by |
|---|---|---|---|
| [#82](https://github.com/jprybylski/deckifyr/issues/82) | Serve the web editor under a path prefix | Done (v0.3.4) | Gate 1 proxy / Gate 3 |
| [#83](https://github.com/jprybylski/deckifyr/issues/83) | `serve` launcher mode: dirty state on `/api/health`, orderly stop | Done (v0.3.4); SIGTERM still loses unsaved edits, so use `POST /api/shutdown` | Gate 1 lifecycle / Gate 3 |
| [#84](https://github.com/jprybylski/deckifyr/issues/84) | Job timeouts, cancellation, isolated LibreOffice profile | Done (v0.3.4; Windows tree-kill tests run since v0.3.5) | Gate 3 |
| [#85](https://github.com/jprybylski/deckifyr/issues/85) | Versioned `--json` result schema with full warnings | Done (v0.3.4) | Gate 3 |
| [#86](https://github.com/jprybylski/deckifyr/issues/86) | Versioned Python wheel with SHA-256 | Done (v0.3.4) | Gate 0 pins; offline wheelhouse (#36) |
| [#88](https://github.com/jprybylski/deckifyr/issues/88) | **Security:** Host allow-list, cross-site rejection, launcher token | Done (v0.3.5); verified against a running server | Gate 3 |
| [#89](https://github.com/jprybylski/deckifyr/issues/89) | Forbid project-file executables and paths outside the project | Done (v0.3.5) as opt-in `--trusted-binaries-only`; Quarto fragments still run code by design (#33) | Gate 3 |
| [#90](https://github.com/jprybylski/deckifyr/issues/90) | Atomic PPTX, previews and manifest; JSON for every `--json` failure | Done (v0.3.5); argparse errors and signals print no JSON; stale preview files can remain | Gate 3 |
| [#91](https://github.com/jprybylski/deckifyr/issues/91) | Releases can't change after publishing; small contract gaps | Done (v0.3.5); GitHub's immutable-releases setting is still off | Gate 0 pins |
| [#93](https://github.com/jprybylski/deckifyr/issues/93) | Offline mode for the R wrappers (`uv run --no-sync`) and `init --from-repo` | Open; the launcher calls the Python CLI directly, so not blocking | Gate 3 (#33), offline (#44) |
| [#94](https://github.com/jprybylski/deckifyr/issues/94) | Accept project YAML that starts with a UTF-8 BOM | Open; adapter writes YAML without a BOM (#33) | Gate 3 |

## jprybylski/quartifyr (public)

#69–#72 (PR #74) and #75–#78 (PR #79) were released in v0.3.2 (2026-11-05) and reviewed.
The follow-ups #81–#82 cover the gaps. The release workflow may still refresh a release
(move the tag, replace the tarball) within 24 hours of publishing; that is intentional, for
quick fixes, so pin a quartifyr release only after that window has passed.

| Issue | What | Status | Needed by |
|---|---|---|---|
| [#69](https://github.com/jprybylski/quartifyr/issues/69) | Noninteractive job contract; fail loudly on partial renders | Done (v0.3.2); exits non-zero only with `--strict` | Gate 3 |
| [#70](https://github.com/jprybylski/quartifyr/issues/70) | `check_report_project()` render-free preflight | Done (v0.3.2); `scripts/check.R --json` added by #78 | Gate 3 |
| [#71](https://github.com/jprybylski/quartifyr/issues/71) | Quarto render timeout and process-tree cleanup | Done (v0.3.2); `quarto render` only; Windows test skipped (documented) | Gate 3 |
| [#72](https://github.com/jprybylski/quartifyr/issues/72) | Offline / pre-provisioned project setup | Done (v0.3.2) | Gate 0 offline audit, Gate 3 |
| [#75](https://github.com/jprybylski/quartifyr/issues/75) | Release #69–#72 as a new version; stop rewriting releases | Done (v0.3.2, with `SHA256SUMS`); refreshes allowed for 24 hours by design | Gate 0 pins |
| [#76](https://github.com/jprybylski/quartifyr/issues/76) | Offline-safe render (no venv re-sync or index access) | Done (v0.3.2) as opt-in `--offline`; pass-2 error relabel doesn't fire (#82) | Gate 3, offline |
| [#77](https://github.com/jprybylski/quartifyr/issues/77) | A failed render leaves the previous draft/final looking complete | Done (v0.3.2) for timeouts and early failures; strict rejections and partial pass-2 failures still leave a DOCX in place (#81) | Gate 3 |
| [#78](https://github.com/jprybylski/quartifyr/issues/78) | Result `schema_version`, input hashes, tool versions; `check.R`; small fixes | Done (v0.3.2); inputs hashed after render and incomplete (#82) | Gate 3 |
| [#81](https://github.com/jprybylski/quartifyr/issues/81) | No complete-looking DOCX after a strict rejection, partial pass-2 failure or kill; `.previous` keeps the last good render | Open; adapter renders in staging meanwhile (#32) | Gate 3 |
| [#82](https://github.com/jprybylski/quartifyr/issues/82) | Pass-2 offline relabel; relative `reference_doc`/style paths; hash inputs before render; schema-check real output; venv mismatch | Open; adapter passes absolute paths and hashes inputs itself (#32) | Gate 3, offline |
| [#83](https://github.com/jprybylski/quartifyr/issues/83) | `--soffice-bin` / `QUARTIFYR_SOFFICE_BIN` for the LibreOffice steps | Open; adapter controls `PATH` meanwhile (#32) | Gate 3 (#32), #8 |
| [#85](https://github.com/jprybylski/quartifyr/issues/85) | Offline report setup without pyro: one Python env for both passes | Open; adapter creates the project `.venv` from the wheelhouse and writes reportifyr's init marker (temporary, #32) | Gate 3 (#32), offline |

Offline installation is a supported v1 use case (2026-11-04). pyro and reportifyr (A2-ai,
external) also reach the network during project setup. opncafyr handles that in its
adapter (#44) rather than through upstream issues.

## Not filed

- **mantra-opnca/opnca (core).** No changes needed: the launcher filters to one analyte
  before opnca-r sees the data, so the core's deferred multi-analyte axis is not required.
- **A2-ai/reportifyr.** External. Sidecar validation lives in the opncafyr publishing
  adapter instead of a reportifyr change. pyro project setup
  (`initialize_report_project()`) and the global git identity it requires are handled in
  the quartifyr adapter ([offline.md](offline.md) R1, R4).
