# opnca-r's own data-format check on one analyte selection (#24). Fixed program: the
# input path, output folder and the exact filter values come from environment variables.
#
#   FORMAT_INPUT            cleaned.csv
#   FORMAT_OUTPUT           folder for format.json
#   FORMAT_ANALYTE_COLUMN / FORMAT_ANALYTE_VALUE     exact-value analyte filter
#   FORMAT_SPECIMEN_COLUMN / FORMAT_SPECIMEN_VALUE   optional exact-value specimen filter
#
# The selection is read the way opnca-r's app reads a CSV (types converted), then passed
# to opnca::check_data_format(). Only roles, column names and opnca-r's issue texts are
# written; never a data value.

env <- function(name) Sys.getenv(name, unset = "")
df <- utils::read.csv(env("FORMAT_INPUT"), colClasses = "character", check.names = FALSE,
                      na.strings = character(0), encoding = "UTF-8")
keep <- df[[env("FORMAT_ANALYTE_COLUMN")]] == env("FORMAT_ANALYTE_VALUE")
if (nzchar(env("FORMAT_SPECIMEN_COLUMN"))) {
  keep <- keep & df[[env("FORMAT_SPECIMEN_COLUMN")]] == env("FORMAT_SPECIMEN_VALUE")
}
df <- df[keep, setdiff(names(df), "opncafyr_line"), drop = FALSE]
df[] <- lapply(df, function(x) utils::type.convert(x, as.is = TRUE, na.strings = c("", "NA")))
report <- opnca::check_data_format(df)
cols <- report$columns
out <- list(
  opnca_version = as.character(utils::packageVersion("opnca")),
  rows = nrow(df),
  standard_detected = if (is.na(report$standard_detected)) NULL else report$standard_detected,
  issues = I(as.character(report$issues)),
  columns = lapply(seq_len(nrow(cols)), function(i) list(
    role = cols$role[i], found_column = if (is.na(cols$found_column[i])) NULL else cols$found_column[i],
    present = isTRUE(cols$present[i]), type_ok = if (is.na(cols$dtype_ok[i])) NULL else cols$dtype_ok[i],
    required = isTRUE(cols$required[i])
  ))
)
dir.create(env("FORMAT_OUTPUT"), showWarnings = FALSE, recursive = TRUE)
jsonlite::write_json(out, file.path(env("FORMAT_OUTPUT"), "format.json"), auto_unbox = TRUE,
                     pretty = TRUE, null = "null")
cat("OPNCAFYR-PROGRESS: opnca-r checked ", nrow(df), " rows\n", sep = "")
