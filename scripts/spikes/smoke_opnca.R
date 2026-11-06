# opnca-r smoke test with a known answer (install spikes #6, #7).
#
#   Rscript --vanilla smoke_opnca.R <private library> [more libraries ...]
#
# Two profiles; P2 is P1 doubled. Points at 2, 4 and 8 h fall on one exponential with
# half-life 2 h, so lambda_z = ln(2)/2 and the half-life is 2 h exactly. Cmax/Tmax are
# read straight off the data. AUClast is 26.25 (linear trapezoid) or 24.8372 (linear-up/
# log-down), P2 twice that.
.libPaths(commandArgs(trailingOnly = TRUE))
suppressPackageStartupMessages(library(opnca))
pk <- data.frame(
  USUBJID = rep(c("P1", "P2"), each = 5),
  ACTUAL_TIME = rep(c(0, 1, 2, 4, 8), 2),
  CONC = c(0, 10, 5, 2.5, 0.625, 0, 20, 10, 5, 1.25),
  DOSE = 100, ROUTE = "extravascular", stringsAsFactors = FALSE
)
fit <- as.data.frame(opnca(pk, metrics = c("cmax", "tmax", "auclast", "lambda_z", "half_life")))
v <- function(id, m) fit$value[fit$profile_id == id & fit$metric == m]
checks <- c(
  cmax_P1 = abs(v("P1", "cmax") - 10) < 1e-12,
  cmax_P2 = abs(v("P2", "cmax") - 20) < 1e-12,
  tmax = v("P1", "tmax") == 1 && v("P2", "tmax") == 1,
  lambda_z = abs(v("P1", "lambda_z") - log(2) / 2) < 1e-9,
  half_life = abs(v("P1", "half_life") - 2) < 1e-9,
  auclast = min(abs(v("P1", "auclast") - c(26.25, 5 + 10 / log(2) + 7.5 / log(4)))) < 1e-6,
  auclast_P2 = abs(v("P2", "auclast") - 2 * v("P1", "auclast")) < 1e-9
)
cat(sprintf("opnca %s (%s) on R %s: %s\n", packageVersion("opnca"), basename(dirname(find.package("opnca"))), getRversion(),
            paste(names(checks), ifelse(checks, "ok", "FAIL"), collapse = ", ")))
cat(sprintf("P1 auclast = %.6f\n", v("P1", "auclast")))
if (!all(checks)) quit(status = 1)
