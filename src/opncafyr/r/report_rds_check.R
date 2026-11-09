# Inspect the formatted tables (.rds) a report would use (#62). Fixed program: everything
# arrives by environment variable, and only a status per file is written, never a value.
#
#   RDS_CHECK_BASE   the report project folder
#   RDS_CHECK_LIST   a text file: one project-relative path per line (copies of published
#                    files, already matched to their publication's SHA-256)
#   RDS_CHECK_OUT    where to write the JSON result
#
# Status per file: ok, captioned (a flextable that carries its own caption), not_flextable,
# or unreadable. The caption text is not written.

env <- function(name) Sys.getenv(name, unset = "")
base <- env("RDS_CHECK_BASE")
listed <- readLines(env("RDS_CHECK_LIST"), encoding = "UTF-8", warn = FALSE)
listed <- listed[nzchar(listed)]

status_of <- function(rel) {
  x <- tryCatch(readRDS(file.path(base, rel)), error = function(e) NULL)
  if (is.null(x)) return("unreadable")
  if (!inherits(x, "flextable")) return("not_flextable")
  cap <- tryCatch(as.character(x$caption$value), error = function(e) character(0))
  if (length(cap) > 0L && any(!is.na(cap) & nzchar(cap))) "captioned" else "ok"
}

files <- lapply(listed, function(rel) list(path = rel, status = status_of(rel)))
jsonlite::write_json(list(schema_version = 1L, files = files), env("RDS_CHECK_OUT"),
                     auto_unbox = TRUE, pretty = TRUE)
