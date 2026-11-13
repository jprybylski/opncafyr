---
title: Reviewing a run (QC)
parent: Guides
nav_order: 4.5
description: How to review a completed opncafyr run and its code, record the evidence, and compare its numbers with PKNCA, with worked examples on the practice file.
permalink: /guides/qc/
---

# Reviewing a run (QC)

A worked procedure for checking a completed run: its files, its replay, the code behind it, and its numbers against an independent program.
{: .fs-5 .fw-300 }

{: .research }
> **Research use only.** This workspace has not been validated for clinical or regulatory decisions. A passed replay, a clean code review and a matching comparison each show something narrower than "the analysis is right". This page says what each one shows.

This page is for a reviewer: the person who checks someone else's run, or their own before it is used. It assumes you know the [run states and replay]({{ '/guides/provenance/' | relative_url }}#runs-are-sealed-replayable-bundles) and the [files in a run folder]({{ '/reference/files/' | relative_url }}).

The examples use the [two-analyte walkthrough]({{ '/get-started/walkthrough/' | relative_url }})'s made-up practice file. Your run IDs and times will differ from the ones shown.

## 1. Decide what the review covers

Five kinds of check are often called QC. They answer different questions, so write down which ones you did.

| Check | What it can show | What it cannot show |
|---|---|---|
| **Data and choices** | The rows, cleaning, mapping, BLQ rules, exclusions and units are the ones the analysis plan asks for | That the calculations are right |
| **Replay** | The saved script, run again on the saved input, gives the saved numbers | That the rows or choices were right, or that the calculations are right |
| **Source review** | The code that ran does what it is meant to, at an identified revision | That this run used it correctly |
| **Independent comparison** | A second program, given the same rows and settings, gets the same numbers | That either program's definitions suit your study |
| **Report or deck** | The files were assembled from the pinned runs as recorded | Anything about the analysis |

The scientific judgement (is this the right analysis?) is yours, and none of these checks replaces it.

### Start from one exact run

Review one completed run, named by its run ID (for example `20261113T013458Z-03a0`), never "the latest". Before anything else:

1. On the **Analysis** page, in **7. Runs**, select **Check files** on the run. It should say "every file matches its recorded hash". If it lists a changed, missing or added file, stop: the run is not the one that was completed.
2. Open the run's `manifest.json` (in `.opncafyr/runs/<run ID>/`) and record:

| Record | Where in `manifest.json` |
|---|---|
| Run ID, state, created | `run_id`, `state` (must be `completed`), `created_at` |
| Source file and its SHA-256 | `source.path`, `source.sha256` |
| Cleaning recipe and its SHA-256 | `recipe.id`, `recipe.sha256` |
| Analyte and specimen (exact raw values) | `analyte.value`, `specimen` |
| Units | `analysis_units` |
| BLQ method and rules | `nca_settings.blq` |
| Exclusions and their reasons | `exclusions` |
| Acknowledged warnings | `acknowledgements` |
| Tool and R versions | `tools` |
| Script, and the replay's result | `script`, `replay` (`status`, `rtol`, `path`, `sha256`) |

A completed run never changes. If the review finds a problem, the fix is a new run (see [Any change makes a new run]({{ '/guides/provenance/' | relative_url }}#any-change-makes-a-new-run)). Never edit files in a run folder.

## 2. Read the replay correctly

A run folder holds the script, the frozen input and every replay:

| File | What it is |
|---|---|
| `analysis.R` | the script opnca-r saved; it reads `input/analysis.csv` and writes `replay/` |
| `input/analysis.csv` | the exact rows the run analysed, after cleaning and mapping |
| `results/` | the saved result tables (and figures) |
| `manifest.json` | the run's record, including its latest replay |
| `logs/replay-<n>/replay.json` | the report of replay number *n* |
| `logs/replay-<n>/replay/` | the tables that replay produced |

The app replays a run by itself after opnca-r saves it. It copies `analysis.R` and `input/analysis.csv` into a separate work folder, runs the script there, and compares each result table with the saved one. To run it again, select **Replay again** on the run in **7. Runs**; each replay gets its own `logs/replay-<n>/` folder, so earlier reports are kept.

In `replay.json`:

- `status` is `passed` or `failed`. A run is completed only after a `passed` replay.
- `rtol` is the relative tolerance, `1e-10` by default: two numbers match when their difference is at most `rtol` times the larger of the two. Identifiers, labels, counts, flags and status columns must match exactly, and so must blanks.
- `tables` lists each compared table with `ok`, `mismatch_count`, `max_rel_diff` and its mismatches (row, column, saved and replayed value).
- `not_reproduced` lists tables that were **not compared**, each with the reason opnca-r gave. Read this list: a table in it has no replay evidence.
- `script_sha256` and `input_sha256` name the exact script and input that were replayed.

{: .note }
> A passed replay shows that the saved script gives the saved numbers from the saved input, with the same tools. It does not check the rows that went in, the choices made, or the calculation methods. Use the other checks for those.

## 3. Choose traceable source material

To review the code that produced a run, you need to know exactly which code that was. The run records versions (`tools` in `manifest.json`, and the header of `analysis.R`); your source must be identified to the same precision.

**A clone of a code repository.** Use it when you have access to the repository. Check out the exact tag or commit for the recorded version, and record the repository's address, the commit ID and `git status` (it should show no local changes). A branch name such as `main` is not enough: it moves.

**A copied folder or an offline bundle.** Use it when you can't reach the repository, for example on a firewalled system. Record where the copy came from and when, and a SHA-256 for each file (or for the archive, if its own fingerprint is published). A folder of unknown origin, or one with untracked edits, is not traceable.

For either route, record:

- the origin and exact revision, or the file fingerprints;
- the runtime and package versions you reviewed against;
- any difference from the versions the run recorded, and why it doesn't matter (or that it does).

Keep code apart from study data. Don't put study data or run folders into a code repository, and don't upload identifiable data, or details of private repositories, to a public site.

[Version history]({{ '/guides/version-history/' | relative_url }}) in a study folder is a separate thing: it keeps Git snapshots of files you choose. It is not how runs are sealed or replayed, and a run's evidence doesn't depend on it.

## 4. Review the code

A compact procedure for a conventional code review:

1. **Fix the revisions.** Name the reviewed revision and the one you compare it with (the last reviewed release, say). Review the difference between them, not a whole tree from memory.
2. **Follow the data.** Trace one row's path: from the input mapping and filters, through the calculation, to the exported tables and the replay. Note where BLQ rules, exclusions, units and the terminal phase are applied.
3. **Read the tests.** Check that the changed behaviour is tested, and that the tests would fail without the change.
4. **Check dependencies.** List changed package versions and lock files, and read the changes that touch calculations.
5. **Run the agreed checks.** Run the project's automated tests and linters as agreed, and keep their output.
6. **Write findings down.** One finding per line, with file and line, what is wrong, and its severity.
7. **Resolve them.** Fix each finding in a reviewed change. If a fix changes any result, make a new run with the fixed version and review that run; the old run stays as it was.

A review record can be as short as this (a made-up example):

| Field | Example |
|---|---|
| Reviewed revision | tag `v1.4.2`, commit `3f9c2e1` |
| Compared with | tag `v1.4.1`, commit `a07b4d8` |
| Scope | summary tables and their labels |
| Reviewer, date | J. Doe, 2026-11-13 |
| Finding 1 | `R/summary.R:57`: a label says "geometric mean" where the value is the arithmetic mean; severity: minor |
| Disposition | fixed in commit `5d1e0aa`; new run `20261114T090211Z-1c2d` |
| Evidence | test output saved as `review/tests-3f9c2e1.txt` |

## 5. Run the script yourself (RStudio)

To look inside a run, run its script yourself, on a copy. RStudio is a convenient way to do this, but it is only a way to look: what counts is the R version and packages you run it with.

1. **Make a review copy.** Make a folder outside the study folder, for example `qc-review/<run ID>/`. Copy into it, keeping the same layout: `analysis.R`, `input/analysis.csv`, `results/`, `manifest.json` and `logs/replay-<n>/replay.json`. Never work in the run folder itself.
2. **Use the run's tools.** Use the R version in `manifest.json` (`tools.R`) and the app's private R library, which holds the opnca-r version the run used. Put any extra package you need, such as PKNCA, in a separate review library; never install into the app's library. If you use a different R or package version, record it: it can change results.
3. **Open the copy in RStudio** and set the working directory to it (**Session**, **Set Working Directory**, **Choose Directory…**). The script reads `input/analysis.csv` relative to that folder.
4. **Run the checks below** in the console.

```r
# In RStudio, with the working directory set to the review copy.
# 1. The copy matches the sealed run.
manifest <- jsonlite::fromJSON("manifest.json")
sealed <- manifest$sealed$files
here <- sealed[file.exists(sealed$path), ]
here$copy_sha256 <- unname(tools::sha256sum(here$path))
print(here[, c("path", "copy_sha256")][here$sha256 != here$copy_sha256, ])  # should be empty
cat("checked", nrow(here), "files\n")

# 2. The same tools as the run.
manifest$tools
sessionInfo()
packageVersion("opnca")

# 3. Run the saved script. It writes replay/ in this copy, never in the run.
source("analysis.R")

# 4. Compare what it wrote with the saved results.
for (f in list.files("results", pattern = "[.]csv$")) {
  saved <- read.csv(file.path("results", f))
  again <- read.csv(file.path("replay", f))
  cat(f, ":", isTRUE(all.equal(saved, again, tolerance = 1e-10)), "\n")
}
```

On the practice file's PARENT run this printed an empty table, `checked 11 files`, the versions `R version 4.6.2` and `0.3.3`, and `TRUE` for each of the three result tables.

After `source("analysis.R")`, the objects the script made are in the RStudio **Environment** pane: the rows it read and the fitted analysis. Look at them there, or with `str()` and `View()`, to see intermediate values. Save `sessionInfo()`'s output with your review.

## 6. Compare with PKNCA

[PKNCA](https://cran.r-project.org/package=PKNCA) is an independent, open-source R package for NCA. Comparing a run with it is a strong check, but only when both programs get **the same rows and the same settings**. Most differences in a first comparison come from settings that weren't aligned, not from either program.

The examples below used R 4.6.2, opnca-r 0.3.3 (the run's version) and PKNCA 0.12.1, on runs of the practice file made as in the walkthrough.

### The comparison script

Save this as `compare-pknca.R` and run it with the working directory set to the review copy from step 5. It writes `pknca-comparison.csv` there and prints what to investigate.

```r
# Compare a completed opncafyr run with PKNCA. Research use only.
# Run it with the working directory set to a review copy of the run folder, which holds
#   input/analysis.csv                 the rows the run analysed
#   results/opnca-parameters-long.csv  the run's saved parameters
#   manifest.json                      the run's record, with its settings
library(PKNCA)

rows <- read.csv("input/analysis.csv", stringsAsFactors = FALSE)
settings <- jsonlite::fromJSON("manifest.json")$nca_settings$effective_settings

# 1. The same reviewed rows: leave out what the run excluded or recorded as missing.
rows <- rows[!rows$NCA_EXCLUDED & !rows$NCA_MISSING, ]

# 2. The run's BLQ rules (manifest.json, nca_settings): BLQ before the first measurable
#    sample counts as 0; BLQ later in the profile is left out.
rows$conc <- ifelse(rows$NCA_BLQ, 0, rows$NCA_CONC)

# 3. The run's other settings, stated rather than left to defaults.
conc <- PKNCAconc(rows, conc ~ AFRLT | USUBJID)
dose <- PKNCAdose(unique(rows[, c("USUBJID", "DOSE")]), DOSE ~ . | USUBJID,
                  route = "extravascular")
intervals <- data.frame(
  start = 0, end = Inf,
  cmax = TRUE, tmax = TRUE, clast.obs = TRUE, tlast = TRUE, auclast = TRUE,
  lambda.z = TRUE, half.life = TRUE, aucinf.obs = TRUE,
  impute = "start_conc0"           # the profile starts at 0 at the dose time
)
options <- list(
  auc.method = "lin up/log down",  # integration: linear up, log down
  conc.blq = list(first = "keep", middle = "drop", last = "drop"),
  # opnca-r's "Exclude Tmax and earlier": unticked lets the Tmax sample (and earlier
  # ones) into the terminal phase. PKNCA can allow Tmax, never earlier samples.
  allow.tmax.in.half.life = !isTRUE(settings$terminal$exclude_tmax)
)
res <- as.data.frame(pk.nca(PKNCAdata(conc, dose, intervals = intervals,
                                      options = options)))

# 4. PKNCA's names for the run's parameters. Only these are compared.
same <- c(cmax = "cmax", tmax = "tmax", clast.obs = "clast", tlast = "tlast",
          auclast = "auclast", lambda.z = "lambda_z", half.life = "half_life",
          aucinf.obs = "aucinf_obs")
pk <- res[res$PPTESTCD %in% names(same), ]
pk <- data.frame(profile_id = pk$USUBJID, metric = unname(same[pk$PPTESTCD]),
                 pknca = pk$PPORRES)

op <- read.csv("results/opnca-parameters-long.csv", stringsAsFactors = FALSE)
op <- data.frame(profile_id = op$profile_id, metric = op$metric,
                 opnca = suppressWarnings(as.numeric(op$value)))
both <- merge(op, pk, by = c("profile_id", "metric"), all.x = TRUE)

# 5. Relative difference against a stated tolerance. Two separate programs, so not
#    the replay's 1e-10.
tolerance <- 1e-6
both$rel_diff <- abs(both$opnca - both$pknca) / pmax(abs(both$opnca), abs(both$pknca))
both$rel_diff[!is.na(both$opnca) & both$opnca == both$pknca] <- 0
both$result <- ifelse(!both$metric %in% same, "not compared",
               ifelse(is.na(both$opnca) & is.na(both$pknca), "agrees (both blank)",
               ifelse(is.na(both$rel_diff), "differs (one blank)",
               ifelse(both$rel_diff <= tolerance, "agrees", "differs"))))
both <- both[order(both$profile_id, both$metric), ]
write.csv(both, "pknca-comparison.csv", row.names = FALSE)

cat(sprintf("PKNCA %s, R %s, tolerance %g\n", packageVersion("PKNCA"),
            getRversion(), tolerance))
print(table(both$result))
cat("\nTo investigate:\n")
print(both[grepl("^differs", both$result), ], row.names = FALSE, digits = 7)
```

The script follows the practice runs' settings, and reads the terminal-phase setting from the run's `manifest.json` (`nca_settings.effective_settings`). Before you use it on another run, check each numbered step against that run's `manifest.json` (`nca_settings`, `analysis_units`, the route) and change what differs. Parameters the run saved that PKNCA doesn't name here are listed as "not compared", never forced into a match.

The tolerance, a relative difference of `1e-6`, is a choice: two programs that apply the same formulas in double precision usually agree to about `1e-12`, so `1e-6` leaves room for rounding but not for a different method. State the tolerance you use in your record.

### Example A: a straightforward profile (PARENT)

**First attempt, PKNCA's default terminal rule.** With `allow.tmax.in.half.life` left at PKNCA's default (`FALSE`), subject `TA-001` gives:

| Parameter | opnca-r | PKNCA | Relative difference |
|---|---|---|---|
| λz (1/h) | 0.0983545 | 0.0977964 | 5.7e-3 |
| t½ (h) | 7.04744 | 7.08766 | 5.7e-3 |
| AUCinf (ng·h/mL) | 2601.23 | 2602.79 | 6.0e-4 |

Cmax, Tmax, Clast, Tlast and AUClast agreed. Only the terminal phase differs, so look at which samples each program used. PKNCA, by default, never uses the Tmax sample for λz: here it used 3 samples from 3.93 h. The run was made with opnca-r's **Exclude Tmax and earlier** unticked (`exclude_tmax: false` in `manifest.json`), so its terminal phase may include the Tmax sample, and the best fit used 4 samples from 2.05 h, the Tmax. That is a setting that wasn't aligned, not a defect.

**Aligned.** With `allow.tmax.in.half.life` set from the run's setting, as in the script, every parameter of `TA-001` agrees:

| Parameter | opnca-r | PKNCA | Relative difference |
|---|---|---|---|
| Cmax (ng/mL) | 230.987 | 230.987 | 0 |
| Tmax (h) | 2.05 | 2.05 | 0 |
| Clast (ng/mL) | 26.9 | 26.9 | 0 |
| Tlast (h) | 23.9333 | 23.9333 | 0 |
| AUClast (ng·h/mL) | 2327.7325 | 2327.7325 | 2.0e-15 |
| λz (1/h) | 0.0983545 | 0.0983545 | 9.9e-16 |
| t½ (h) | 7.04744 | 7.04744 | 2.5e-16 |
| AUCinf (ng·h/mL) | 2601.2329 | 2601.2329 | 3.5e-16 |

Over the whole PARENT run, the script printed:

```text
PKNCA 0.12.1, R 4.6.2, tolerance 1e-06

             agrees agrees (both blank)             differs
                 70                   8                   2

To investigate:
 profile_id     metric    opnca   pknca    rel_diff  result
     TA-003 aucinf_obs 1916.074 1903.66 0.006478937 differs
     TA-003    auclast 1809.804 1797.39 0.006859374 differs
```

The 8 blanks are subject `TA-008`, whose whole profile is excluded; both programs report nothing for it. The two differences are investigated below.

### Example B: BLQ samples and a short terminal phase (METAB)

METAB has the practice file's harder profiles: results reported as `<LLOQ`, a concentration of 0 with no BLQ flag, and profiles with few samples after Tmax. The same script printed:

```text
             agrees agrees (both blank)             differs differs (one blank)
                 50                  17                   4                   9
```

The 13 rows to investigate fall into three groups.

**Subject `TA-003`: BLQ after the first samples.** Its results from 1.93 h on are `<LLOQ`. Under the run's rules both programs leave them out, so both use the two measurable samples. AUClast still differs: 3.740978 (opnca-r) against 3.439728 (PKNCA). The cause is the same as PARENT `TA-003`, below, and so is the way to align it.

**Subject `TA-005`: a measured 0.** Its 24.05 h result is `0` with no BLQ flag; the checks raised this as a warning ("conc zero not blq"), and the walkthrough acknowledges it.

| Parameter | opnca-r | PKNCA |
|---|---|---|
| Clast (ng/mL) | 0 | 44.742 |
| Tlast (h) | 24.05 | 11.9333 |
| AUClast (ng·h/mL) | 800.377 | 529.315 |

PKNCA takes Clast as the last concentration above zero; opnca-r took the measured 0 as the last concentration. The AUClast difference, 271.06, is exactly the area of the straight line from 44.742 at 11.93 h down to 0 at 24.05 h. Whether that 0 is a real measurement or an unflagged BLQ result is a question about the data. If it is BLQ, flag it as BLQ in the mapping or cleaning and make a new run: the run's BLQ rules then leave it out, and opnca-r gives the same Clast, Tlast and AUClast as PKNCA. We checked this by running the same analysis with that one sample flagged.

**Subjects `TA-002`, `TA-004` and `TA-007`: two samples after Tmax.** For each, PKNCA reports no λz, t½ or AUCinf ("Too few points for half-life calculation"): from Tmax on there are only 2 samples, and it needs 3. opnca-r reports a λz for each. Fitting the candidate samples by hand shows which it used: for `TA-002`, λz = 0.101924 1/h is the fit to the last 3 samples, from 3.98 h, while Tmax is at 7.92 h. So the fit includes a sample before Tmax. `TA-004` and `TA-007` show the same pattern.

That is the run's setting at work: with **Exclude Tmax and earlier** unticked, opnca-r may use Tmax and earlier samples. PKNCA's nearest option, `allow.tmax.in.half.life = TRUE`, admits the Tmax sample but never an earlier one, so for these three profiles no PKNCA setting reproduces this run. To compare the terminal phase like for like, compare a run made with **Exclude Tmax and earlier** ticked (in opnca-r's terminal-phase settings) against PKNCA's default: the script then sets `allow.tmax.in.half.life = FALSE` by itself. We checked that pairing by running the same analysis with that setting: every λz and t½ then agreed, for both analytes, and so did every AUCinf except PARENT `TA-003`'s, which differs only by its AUC start (below).

### Investigating a difference

Work through these in order, and stop at the first that explains it. Only call a result a defect once the rest are ruled out.

1. **Rows.** Do both programs get the same rows? Compare row counts per subject; check exclusions, missing values and the BLQ flag column.
2. **BLQ handling.** Which BLQ samples become 0 and which are left out, at the start, in the middle and at the end of a profile?
3. **Start of the profile.** Where does AUC start: the dose time, or the first sample? Is a pre-dose sample at a negative time used?
4. **Interpolation.** Linear, log-down, or linear-up/log-down; how zeros are handled.
5. **Terminal phase.** Which samples each program used for λz, and its rules: minimum number of samples, whether Tmax (or earlier samples) may be used, the adjusted R² rule.
6. **Units.** The same units for concentration, time and dose, and the same units in the output.
7. **Versions.** The R, opnca-r and PKNCA versions, and the run's own settings in `manifest.json`.

For PARENT `TA-003`, step 3 explains it. Its pre-dose sample is at −0.25 h (the checks raised this as a warning, "time negative", which the walkthrough acknowledges) and its first measurable sample (99.313 ng/mL) at 0.4833 h. Integrating by hand gives AUClast = 1809.804250 from −0.25 h, which is opnca-r's value, and 1797.390125 from 0 h, which is PKNCA's. Both programs used the same samples; they start the area at different times. To align them, start PKNCA's interval at each subject's first sample time when that is before the dose (an `intervals` table with a `USUBJID` column and `start = pmin(first sample time, 0)`): AUClast and AUCinf then agree. PKNCA then reports Tmax and Tlast from the interval start, so they read 0.25 h later than opnca-r's, which count from the dose.

Every difference above came from a setting or a definition that can be aligned: the Tmax rule, where the profile starts, how a pre-dose sample before the dose counts, whether a measured 0 is BLQ, and whether samples before Tmax may enter the terminal phase. With all of them aligned, every compared value agreed, apart from the 0.25 h by which PKNCA's Tmax and Tlast for `TA-003` are counted from the earlier interval start. Record in your review which settings you aligned and how, and which definition the analysis plan calls for. A difference you can't align with any setting is an open discrepancy: record it with its evidence and raise it. A comparison like this one never shows that two programs are equivalent in general: only that, for these rows and settings, they agree where the table says so.

## 7. Record the review

Keep one record per reviewed run, next to your review copy and outside the run folder. A template:

| Item | Entry |
|---|---|
| Run ID and state | `20261113T013458Z-03a0`, completed |
| **Check files** result, date | every file matches its recorded hash, 2026-11-13 |
| Source, recipe and input fingerprints | from `manifest.json` (step 1) |
| Settings reviewed | analyte, units, BLQ rules, exclusions, acknowledged warnings |
| Replay evidence | `logs/replay-1/replay.json`: `passed`, `rtol` 1e-10, its SHA-256; tables not reproduced: none |
| Source reviewed | origin, revision or file fingerprints; differences from the run's versions |
| Code review | link to the review record (step 4) |
| Session | R version, opnca-r version, review library, `sessionInfo()` saved as … |
| Independent comparison | PKNCA version, script and its SHA-256, `pknca-comparison.csv`, tolerance |
| Open discrepancies | each with its evidence and owner |
| Conclusion | what was checked, what was found, what is still open |
| Replacement run | the new run ID, if the review led to one |
| Reviewer, date | |

## Related pages

- [Workspace and run provenance]({{ '/guides/provenance/' | relative_url }}): what is recorded, replay and sealing.
- [Files and folders]({{ '/reference/files/' | relative_url }}): the run folder in full.
- [Cleaning, BLQ and units]({{ '/guides/cleaning/' | relative_url }}): how BLQ rules and units are set.
- [The two-analyte walkthrough]({{ '/get-started/walkthrough/' | relative_url }}): makes the runs used in the examples.
