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
| [#45](https://github.com/mantra-opnca/opnca-r/issues/45) | macOS binaries require macOS 15 (no `MACOSX_DEPLOYMENT_TARGET`); match R 4.6's macOS 13 | Done (PR #48, v0.2.8); `manifest.json` `macos_min` is 13.0 (arm64) and 11.0 (x86_64), checked with `otool`. Our minimum stays macOS 15 until Quarto, uv Python, wheels and CRAN binaries are rechecked (#1) | Gate 0 |
| [#46](https://github.com/mantra-opnca/opnca-r/issues/46) | Save lock across processes; `blq_method` and role-guess defaults; record every effective `opnca()` setting | Done (PR #48, v0.2.8): save lock (409 `SAVE_IN_PROGRESS`), `INCOMPLETE_SAVE` for a folder without `COMPLETE`, `--require-explicit-settings`, `--no-guess-roles`, `effective_settings`. `--no-guess-roles` is page-only (the API still defaults omitted roles to `USUBJID` etc.), and a killed save leaves `.opnca-save.lock/` in the run folder. Adapter still to switch to the new flags (#27, #28) | Gate 2 |
| [#47](https://github.com/mantra-opnca/opnca-r/issues/47) | `POST /api/v1/shutdown` reply sometimes cut off before the server stops | Done (PR #48, v0.2.8); 130 of 130 full replies on macOS arm64 (100 from the PR build, 30 from the release binary). The adapter's truncation tolerance can go (#27) | Gate 2 |
| [#49](https://github.com/mantra-opnca/opnca-r/issues/49) | Excluding a point in the app replaces the preset's `excluded` column with an all-FALSE `OPNCA_EXCLUDED`, dropping the launcher's exclusions | Done (PR #51, v0.2.9): the first in-app exclusion seeds `OPNCA_EXCLUDED` from the mapped column (source `mapping`), and the record keeps `mapping.excluded`. The launcher keeps its check, now reports in-app points by source, and the replay reproduces both sets (#28) | Gate 2 |
| [#50](https://github.com/mantra-opnca/opnca-r/issues/50) | Save a mean concentration–time figure (linear and log) with each run | Done (PR #51, v0.2.9): `mean-linear`/`mean-log`, `results/opnca-mean-profiles.csv` (compared on replay) and a `mean_profiles` record block. No default time basis: the save is refused until one is chosen. The curator pre-selects the figures and states the choices in their sidecars (#31, ADR 0007 amendment) | Gate 3 |
| [#32](https://github.com/mantra-opnca/opnca-r/issues/32) | Caller-declared unit labels throughout the R API (`opnca_units()`) | Done (v0.3.0), reviewed 2026-11-08. Labels only, no conversion. The app half was missing: a launcher could not pass labels, so #56 was filed | #57 |
| [#53](https://github.com/mantra-opnca/opnca-r/issues/53) | Saved `analysis.R` states "research use only" in its header, like the launcher's cleaning script | Done (v0.3.0). It is a comment, so replays are unchanged | #40 |
| [#54](https://github.com/mantra-opnca/opnca-r/issues/54) | A self-describing export manifest (what each saved file is, its settings, subject-level or not, replayed or not), one registration path for every view's exports, CSV plus optional flextable `.rds` tables, and `analysis.R` replay for every saved table (dose proportionality first) | Done (v0.3.0): `artifacts.json` schema 2, additive; `results/tables/*.rds` whenever flextable is installed (it is, in our lock). Replay skips tables marked `replay.reproduced: false` (#57). The curator builds its catalog from the manifest, and offers the `.rds` tables as "(formatted)" entries (#59) | Publishing (#31) |
| [#56](https://github.com/mantra-opnca/opnca-r/issues/56) | Launcher-declared unit labels: preset `units`, read-only page fields, `UNITS_LOCKED`, `--require-explicit-units`, `effective_settings.units_source` | Done (v0.3.1), checked against a running server (`tests/integration/nca/test_units_r.py`): the preset's labels reach the record, `analysis.R` and the result tables, and estimates are unchanged. `--require-explicit-settings` now implies the units requirement. Its `preset_mapping` config omits `units`; the page config has `units` and `units_source` instead | #57 |
| [#57](https://github.com/mantra-opnca/opnca-r/issues/57) | Honour `--no-guess-roles` on the server (`ROLE_REQUIRED`) and in the Bioequivalence view | Done (v0.3.1). The preset also takes `period`, `treatment` and `sequence`; the launcher doesn't set them, since it runs NCA only | #27 |
| [#58](https://github.com/mantra-opnca/opnca-r/issues/58) | `artifacts.json` says whether a saved file shows subject identifiers (`identifies_subjects`, separate from `subject_level`) | Done (v0.3.1). Boxplots are `identifies_subjects: false`. The curator's "shows subject IDs" is this flag for runs saved by 0.3.1 (#59) | Publishing (#31) |
| [#59](https://github.com/mantra-opnca/opnca-r/issues/59) | Leave the flextable caption off the exported `.rds` tables, so reportifyr can place them (it can't match a captioned table to its `{rpfy}` placeholder; a caption is what leaves it unfilled, confirmed with the real tools) | Done (v0.3.2, 2026-11-09, pinned in #62), as asked; the table fills without the caption (developer machine, not yet the container image). The report check and render still refuse a captioned `.rds` (`workflows/nca/publish/rds_check.py`), which now means a run saved by an earlier opnca-r | #62 |

Gaps still open after 0.3.1: a killed save leaves `.opnca-save.lock/` for 600 s (#46).

Select assets by `manifest.json` fields, never by file name. Since v0.2.7 every binary is
`opnca_<ver>_R<minor>_<platform>-<arch>.<ext>`. Rename it to `opnca_<ver>.<ext>` before
`R CMD INSTALL` or a renv cellar restore.

## jprybylski/deckifyr (public)

#82–#86 were released in v0.3.4 (PR #87), #88–#91 in v0.3.5 (PR #92, 2026-11-06) and
#93–#94 in v0.3.6 (PR #95, 2026-11-07) and #96 in v0.3.7 (2026-11-09). All were reviewed except v0.3.7, which was pinned after its asset hashes matched the release's `SHA256SUMS` and a diff of the fix. Remaining gaps are handled in
our adapter (#33).

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
| [#93](https://github.com/jprybylski/deckifyr/issues/93) | Offline mode for the R wrappers (`uv run --no-sync`) and `init --from-repo` | Done (v0.3.6); also adds `python_env` and the web/preview packages in the R-written group. The launcher still calls the Python CLI directly | Gate 3 (#33), offline (#44) |
| [#96](https://github.com/jprybylski/deckifyr/issues/96) | Build fails with `IndexError` when a table cell is empty (opnca-r's tables have empty cells) | Done (v0.3.7, 2026-11-09), pinned in #62. Empty header and data cells stay empty in the PPTX. The offline flow's deck holds tables again (`scripts/offline/flow_check.py`). A formatted `.rds` table still has to be a `reportifyr` element, not `table`: that is the adapter's job (#62) | Gate 3 |
| [#94](https://github.com/jprybylski/deckifyr/issues/94) | Accept project YAML that starts with a UTF-8 BOM | Done (v0.3.6); all project YAML/JSON/CSV/QMD reads drop a BOM, and reads no longer fall back to the Windows locale encoding | Gate 3 |
| [#97](https://github.com/jprybylski/deckifyr/issues/97) | Record project-relative paths in `deck-manifest.json` | Open (filed 2026-11-08, found by the container image check, #42). Its input, element and output paths are absolute, so a deck build names the folder it was built in (a container's `/workspace`, or a user's home folder). Until it is pinned, `scripts/container/check_image.sh` excepts that one file from the path scan (labelled temporary); our own manifests are relative | #42 |

## jprybylski/quartifyr (public)

#69–#72 (PR #74) and #75–#78 (PR #79) were released in v0.3.2 (2026-11-05) and reviewed.
The follow-ups #81–#86 (PR #87) were reviewed and released in v0.3.3 (2026-11-07). v0.3.4 (2026-11-09) releases #88; it was pinned inside the 24 hour window, so its hashes and tag are to be re-checked after 2026-11-10T19:53Z. The release workflow may still refresh a release
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
| [#81](https://github.com/jprybylski/quartifyr/issues/81) | No complete-looking DOCX after a strict rejection, partial pass-2 failure or kill; `.previous` keeps the last good render | Done (v0.3.3); also moves `draft/intermediate_files/` aside. A non-strict incomplete render is still delivered live, so the adapter keeps `--strict` | Gate 3 |
| [#82](https://github.com/jprybylski/quartifyr/issues/82) | Pass-2 offline relabel; relative `reference_doc`/style paths; hash inputs before render; schema-check real output; venv mismatch | Done (v0.3.3); `inputs[].role` is now an open string within v1 | Gate 3, offline |
| [#83](https://github.com/jprybylski/quartifyr/issues/83) | `--soffice-bin` / `QUARTIFYR_SOFFICE_BIN` for the LibreOffice steps | Done (v0.3.3) | Gate 3 (#32), #8 |
| [#84](https://github.com/jprybylski/quartifyr/issues/84) | Field recalculation always times out on macOS (LibreOffice 26.2) | Done (v0.3.3), verified upstream on macOS 26.6 only; recalculation stays off in the adapter until re-verified (#8) | Gate 3 (#32), #8 |
| [#85](https://github.com/jprybylski/quartifyr/issues/85) | Offline report setup without pyro: one Python env for both passes | Done (v0.3.3) as `initialize_report_project_offline()`; uv is still required (reportifyr runs it). The adapter uses it (#32); the `.venv` and init-marker workaround was never needed | Gate 3 (#32), offline |
| [#86](https://github.com/jprybylski/quartifyr/issues/86) | Field recalculation hangs and orphans soffice on Windows (malformed profile URL) | Done (v0.3.3), with process tests run upstream on Windows 11; recalculation stays off in the adapter until re-verified (#8) | Gate 3 (#32), #8 |
| [#88](https://github.com/jprybylski/quartifyr/issues/88) | Fit wide tables to the page width (reportifyr fixes it at 6 in and squeezes columns) and support landscape sections | Done (v0.3.4, 2026-11-09, pinned in #62): tables fit the page (`fit_tables`, on by default) and `{{< landscape >}}` / `{{< portrait >}}` start and end a landscape section. A table still too wide at 7 pt is a warning, which our `--strict` render rejects; the message carries it. Figures still use `default_fig_width` (`use_artifact_size: false` in the bundled `config.yaml`, and `check` warns about an older project that doesn't), because reportifyr sizes them. Not yet measured on a real DOCX | #62 |

Offline installation is a supported v1 use case (2026-11-04). pyro and reportifyr (A2-ai,
external) also reach the network during project setup. opncafyr handles that in its
adapter (#44) rather than through upstream issues.

## jprybylski/gitneighbr (public)

Filed 2026-11-08 for #56 and released in gitneighbr 0.2.0 (2026-11-08; PRs #45, #46): a
**managed mode** for launchers. Standalone `open_repo()` is
unchanged. The adapter needs 0.2.0 or later.

| Issue | What | Status | Needed by |
|---|---|---|---|
| [#35](https://github.com/jprybylski/gitneighbr/issues/35) | Serve the UI and API under a path prefix | Done (v0.2.0); e2e behind a full-path proxy | #56 proxy |
| [#36](https://github.com/jprybylski/gitneighbr/issues/36) | Foreground `scripts/serve.R`, `GITNEIGHBR_READY` line, two-token shutdown, health `version`/`busy`/`managed` | Done (v0.2.0) | #56 adapter |
| [#37](https://github.com/jprybylski/gitneighbr/issues/37) | Token in an injected `X-Gitneighbr-Token` header; cross-site refusal; no URL-fragment token | Done (v0.2.0); standalone keeps the fragment, removed from history | #56 |
| [#38](https://github.com/jprybylski/gitneighbr/issues/38) | Act only on the exact folder; never an enclosing repository | Done (v0.2.0) | #56 |
| [#39](https://github.com/jprybylski/gitneighbr/issues/39) | Server-side feature profiles; no `gh`/GitHub API traffic when off | Done (v0.2.0); policy flags now enforced on the server in every mode | #56, offline |
| [#40](https://github.com/jprybylski/gitneighbr/issues/40) | Trusted Git with a locked-down config and environment; fail closed on unreviewed repo settings and filters | Done (v0.2.0), plus `--git-exec-path` for the non-relocatable dugite build; hostile-repo tests | #56 |
| [#41](https://github.com/jprybylski/gitneighbr/issues/41) | Selected-file commit on a temporary index; re-check paths; compare-and-swap HEAD | Done (v0.2.0) (all modes) | #56 |
| [#42](https://github.com/jprybylski/gitneighbr/issues/42) | Launcher policy: groups, protected paths, review labels, send-blocked, `.gitignore` template at setup only | Done (v0.2.0) | #56 |
| [#43](https://github.com/jprybylski/gitneighbr/issues/43) | Send/Update with a full, preview-bound history check; HTTPS token via env only | Done (v0.2.0); tested against a real HTTPS `git http-backend` | #56 remote |
| [#44](https://github.com/jprybylski/gitneighbr/issues/44) | `SHA256SUMS` on releases; `clone --`; sanitised PR stderr | Done (v0.2.0) | pins |
| [#48](https://github.com/jprybylski/gitneighbr/issues/48) | Say when Git refuses the folder because another user owns it (`OWNERSHIP`), instead of `GIT_DIR_INVALID` | Open (filed 2026-11-09). Temporary workaround here: the launcher writes one `safe.directory` entry for the exact study folder into its own `git_config` file (`trust_study_folder`, #62); the container start also warns when the owner differs | #62 |

Pinned as a cellar package (`[packages.gitneighbr]`, 0.2.1: PR #47 removed GitHub wording
from the managed status summary, found while making the guide's screenshots). Testing on Windows CI also
added a managed system configuration that keeps Git for Windows' line-ending and
certificate defaults (ADR 0008). Its release workflow re-tags and replaces the tarball when
a later merge leaves `VERSION` unchanged, so pin only a release whose version was bumped.

## Not filed

- **mantra-opnca/opnca (core).** No changes needed: the launcher filters to one analyte
  before opnca-r sees the data, so the core's deferred multi-analyte axis is not required.
- **A2-ai/reportifyr.** External. Sidecar validation lives in the opncafyr publishing
  adapter instead of a reportifyr change. pyro project setup
  (`initialize_report_project()`) and the global git identity it requires are handled in
  the quartifyr adapter ([offline.md](offline.md) R1, R4).
