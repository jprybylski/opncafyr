# Offline CI (#44): one opnca-r calculation on one analyte, with no network.
#
#   Rscript --vanilla opnca_check.R <out.json>
#
# Uses the theophylline sample that ships inside opnca-r (one analyte, clean). The
# two-analyte fixture goes through cleaning (#22) and the analyte snapshot (#25) first,
# which don't exist yet; the follow-up to #44 switches this step to the opnca adapter.

args <- commandArgs(trailingOnly = TRUE)
if (length(args) != 1) stop("usage: opnca_check.R <out.json>", call. = FALSE)

input <- system.file("extdata", "theoph_full.csv", package = "opnca", mustWork = TRUE)
data <- utils::read.csv(input)
result <- opnca::opnca(
  data,
  id = "USUBJID",
  time = "ACTUAL_TIME",
  concentration = "CONC",
  dose = "DOSE",
  route = "ROUTE"
)
table <- as.data.frame(result)
if (nrow(table) == 0) stop("opnca returned no rows", call. = FALSE)

# Cmax is the largest observed concentration, so it can be checked against the input
# exactly. The other metrics are opnca-r's to get right (it is the calculation authority).
cmax <- table[table$metric == "cmax", c("profile_id", "value")]
observed <- tapply(data$CONC, data$USUBJID, max)
if (nrow(cmax) != length(observed)) stop("expected one Cmax per subject", call. = FALSE)
if (!isTRUE(all.equal(sort(unname(cmax$value)), sort(unname(as.numeric(observed)))))) {
  stop("Cmax does not match the observed maxima", call. = FALSE)
}

summary <- list(
  opnca_version = as.character(utils::packageVersion("opnca")),
  input_rows = nrow(data),
  subjects = length(unique(data$USUBJID)),
  result_rows = nrow(table),
  cmax_checked = nrow(cmax),
  result_columns = names(table)
)
jsonlite::write_json(summary, args[[1]], auto_unbox = TRUE, pretty = TRUE)
cat("opnca rows:", nrow(table), "\n")
