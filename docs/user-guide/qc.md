# Reviewing a run (QC)

> **Research use only.** This workspace has not been validated for clinical or regulatory decisions. A passed replay, a clean code review and a matching comparison each show something narrower than "the analysis is right".

This chapter is for a reviewer: the person who checks a completed run before it is used. The website has the same procedure with worked examples on the practice file.

## What each check shows

| Check | What it can show | What it cannot show |
|---|---|---|
| **Data and choices** | The rows, cleaning, mapping, BLQ rules, exclusions and units are the ones the analysis plan asks for | That the calculations are right |
| **Replay** | The saved script, run again on the saved input, gives the saved numbers | That the rows or choices were right, or that the calculations are right |
| **Source review** | The code that ran does what it is meant to, at an identified revision | That this run used it correctly |
| **Independent comparison** | A second program, given the same rows and settings, gets the same numbers | That either program's definitions suit your study |
| **Report or deck** | The files were assembled from the pinned runs as recorded | Anything about the analysis |

The scientific judgement is yours; none of these checks replaces it.

## Start from one exact run

1. Review one completed run, named by its run ID, never "the latest".
2. On the **Analysis** page, in **7. Runs**, select **Check files** on the run. It should say "every file matches its recorded hash". If not, stop.
3. From the run's `manifest.json` (in `.opncafyr/runs/<run ID>/`), record: `run_id` and `state`; `source` and `recipe` with their SHA-256; `analyte` and `specimen`; `analysis_units`; `nca_settings.blq`; `exclusions`; `acknowledgements`; `tools`; `script`; and `replay`.

A completed run never changes. If the review finds a problem, the fix is a new run (see [Analyte runs in opnca-r](analyte-runs.md)). Never edit files in a run folder.

## Read the replay

The app replays a run after opnca-r saves it: it runs `analysis.R` on a copy of `input/analysis.csv` in a separate folder and compares each table with `results/`. **Replay again** on the run in **7. Runs** runs it again; each replay keeps its own `logs/replay-<n>/` folder.

In `logs/replay-<n>/replay.json`:

- `status` is `passed` or `failed`.
- `rtol` is the relative tolerance (default `1e-10`). Identifiers, labels, counts, flags, status columns and blanks must match exactly.
- `tables` lists each compared table, with its mismatches.
- `not_reproduced` lists tables that were **not compared**, with the reason. They have no replay evidence.
- `script_sha256` and `input_sha256` name the exact script and input.

A passed replay shows that the saved script gives the saved numbers. It does not check the rows, the choices or the calculation methods.

## Source you can trace

- **A clone of a code repository:** check out the exact tag or commit for the recorded version; record the address, the commit ID and that `git status` shows no local changes. A branch name is not enough.
- **A copied folder or offline bundle:** record where it came from and a SHA-256 for each file. A folder of unknown origin, or one with untracked edits, is not traceable.

For either, record the runtime and package versions you reviewed against, and any difference from the versions the run recorded. Keep code apart from study data, and never upload identifiable data or private repository details to a public site. Version history in a study folder is separate from run sealing and replay.

## Review the code

1. Name the reviewed revision and the one you compare it with; review the difference.
2. Follow one row from the mapping and filters, through the calculation, to the exported tables and the replay.
3. Read the tests for the changed behaviour.
4. Check changed dependencies and lock files.
5. Run the agreed automated checks and keep their output.
6. Write each finding with file, line, what is wrong and its severity.
7. Fix findings in a reviewed change. If a fix changes a result, make a new run and review that run.

## Run the script yourself (RStudio)

1. Make a folder outside the study folder, such as `qc-review/<run ID>/`. Copy in `analysis.R`, `input/analysis.csv`, `results/`, `manifest.json` and `logs/replay-<n>/replay.json`, keeping the same layout. Never work in the run folder.
2. Use the R version in `manifest.json` (`tools.R`) and the app's private R library, which holds the run's opnca-r version. Put other packages, such as PKNCA, in a separate review library, never in the app's library. Record any different version: it can change results.
3. In RStudio, set the working directory to the copy (**Session**, **Set Working Directory**, **Choose Directory…**) and run:

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

The script's objects then appear in the **Environment** pane for inspection. Save the `sessionInfo()` output with your review.

## Compare with PKNCA

PKNCA is an independent R package for NCA, available from CRAN. A comparison means something only when both programs get the same rows and the same settings. Save this as `compare-pknca.R` and run it in the review copy:

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

The script follows the practice file's runs. Check each numbered step against your run's `manifest.json` and change what differs. Parameters PKNCA doesn't name here are "not compared", never forced into a match.

On the practice file's PARENT run (R 4.6.2, opnca-r 0.3.3, PKNCA 0.12.1), 70 values agreed to about `1e-15`. With PKNCA's default terminal rule, λz differed for several subjects by up to 0.6%: the run had opnca-r's **Exclude Tmax and earlier** unticked, so its terminal phase may use the Tmax sample. The script sets PKNCA's rule from the run's `manifest.json`. The other differences also came from settings or definitions that can be aligned, and agreed once they were:

- **AUC start** (subject `TA-003`): its pre-dose sample is before the dose; start PKNCA's interval at that sample's time. PKNCA then reports Tmax and Tlast from the interval start.
- **A measured 0 with no BLQ flag** (METAB `TA-005`): if it is BLQ, flag it in the mapping or cleaning and make a new run.
- **Samples before Tmax** (METAB `TA-002`, `TA-004`, `TA-007`): with **Exclude Tmax and earlier** unticked, opnca-r may also use samples before Tmax, which no PKNCA setting does. Compare a run made with the box ticked against PKNCA's default.

The website's QC guide works through each.

## Investigating a difference

Stop at the first step that explains it, and only call a result a defect once the rest are ruled out:

1. **Rows:** the same rows per subject; exclusions, missing values, BLQ flags.
2. **BLQ handling:** which BLQ samples become 0 and which are left out.
3. **Start of the profile:** the dose time or the first sample; pre-dose samples at negative times.
4. **Interpolation:** linear, log-down or linear-up/log-down; how zeros are handled.
5. **Terminal phase:** which samples each program used for λz, and its rules.
6. **Units:** for concentration, time, dose and the output.
7. **Versions:** R, opnca-r and PKNCA, and the run's settings.

Record the settings you aligned and the definition the analysis plan calls for. A difference no setting aligns is an open discrepancy: record it with its evidence and raise it. A comparison never shows that two programs are equivalent in general.

## Record the review

| Item | Entry |
|---|---|
| Run ID and state | |
| **Check files** result, date | |
| Source, recipe and input fingerprints | |
| Settings reviewed | analyte, units, BLQ rules, exclusions, acknowledged warnings |
| Replay evidence | `replay.json` status, `rtol`, SHA-256, tables not reproduced |
| Source reviewed | origin, revision or file fingerprints, differences from the run's versions |
| Code review | link to the review record |
| Session | R and opnca-r versions, review library, saved `sessionInfo()` |
| Independent comparison | PKNCA version, script and its SHA-256, results, tolerance |
| Open discrepancies | each with its evidence and owner |
| Conclusion | what was checked, found and still open |
| Replacement run | the new run ID, if any |
| Reviewer, date | |

Keep the record next to your review copy, outside the run folder.
