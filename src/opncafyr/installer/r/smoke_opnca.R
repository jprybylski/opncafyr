# Installation smoke test (#36): one opnca-r calculation with a known answer.
#
#   Rscript --vanilla smoke_opnca.R
#
# Two synthetic profiles (no study data); P2 is P1 doubled. The points at 2, 4 and 8 h lie
# on one exponential with half-life 2 h, so lambda_z = ln(2)/2 and t1/2 = 2 h exactly; Cmax
# and Tmax are read off the data; AUClast is the linear-up/log-down value 24.837056812223246
# (opnca-r's default in 0.2.7 and 0.2.8; 26.25 with the linear trapezoid), P2 twice that. The same
# reference values as the install spikes (docs/spikes/macos-install.md).
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
  cmax = abs(v("P1", "cmax") - 10) < 1e-12 && abs(v("P2", "cmax") - 20) < 1e-12,
  tmax = v("P1", "tmax") == 1 && v("P2", "tmax") == 1,
  lambda_z = abs(v("P1", "lambda_z") - log(2) / 2) < 1e-9,
  half_life = abs(v("P1", "half_life") - 2) < 1e-9,
  auclast = min(abs(v("P1", "auclast") - c(26.25, 5 + 10 / log(2) + 7.5 / log(4)))) < 1e-6,
  auclast_P2 = abs(v("P2", "auclast") - 2 * v("P1", "auclast")) < 1e-9
)
cat(sprintf("opnca %s: %s; AUClast(P1) = %.6f\n", packageVersion("opnca"),
            paste(names(checks), ifelse(checks, "ok", "FAIL"), collapse = ", "), v("P1", "auclast")))
if (!all(checks)) quit(status = 1)
