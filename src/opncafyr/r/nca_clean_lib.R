# opncafyr cleaning library (#22). Fixed, reviewed code: a recipe supplies data only.
#
# Every value is read as text and stays text. A step changes values only as its name
# says, never drops a row unless it is filter_rows, and reports rows in, rows out, rows
# rejected and the lines it touched. Uses base R and jsonlite only.

oc_missing <- c("", "NA", ".", "NaN", "NULL")
oc_number_re <- "^[+-]?([0-9]+\\.?[0-9]*|\\.[0-9]+)([eE][+-]?[0-9]+)?$"
oc_integer_re <- "^[+-]?[0-9]+$"
oc_line <- "opncafyr_line"
oc_excluded <- "opncafyr_excluded"
oc_reason <- "opncafyr_exclusion_reason"
oc_fixed <- c(oc_line, oc_excluded, oc_reason)

oc_conc <- c("pg/mL" = 1e-3, "ng/L" = 1e-3, "ng/mL" = 1, "ug/L" = 1, "ug/mL" = 1e3,
             "mg/L" = 1e3, "mg/mL" = 1e6, "g/L" = 1e6)
oc_amount <- c("ng" = 1e-6, "ug" = 1e-3, "mg" = 1, "g" = 1e3)
oc_time <- c("s" = 1, "min" = 60, "h" = 3600, "d" = 86400, "w" = 604800)

oc_stop <- function(...) stop(paste0(...), call. = FALSE)

# Read every column as text. Physical line numbers come from the file itself, so a
# finding can point at the line a person sees in an editor (the header is line 1).
oc_read <- function(path, sep, sample_rows) {
  con <- file(path, open = "rb")
  raw <- readLines(con, encoding = "UTF-8", warn = FALSE)
  close(con)
  if (length(raw) == 0L) oc_stop("The file is empty.")
  raw[1] <- sub("^﻿", "", raw[1])
  raw <- sub("\r$", "", raw)
  keep <- which(nzchar(raw))
  if (length(keep) == 0L || keep[1] != 1L) oc_stop("The first line must be the header.")
  data_lines <- keep[-1]
  if (sample_rows > 0L && length(data_lines) > sample_rows) {
    data_lines <- data_lines[seq_len(sample_rows)]
  }
  text <- raw[c(1L, data_lines)]
  fields <- count.fields(textConnection(text), sep = sep, quote = "\"",
                         comment.char = "", blank.lines.skip = FALSE)
  if (any(is.na(fields))) {
    oc_stop("A quoted field spans more than one line. Fix the file so each row is one line.")
  }
  ragged <- which(fields != fields[1])
  if (length(ragged) > 0L) {
    lines <- c(1L, data_lines)[ragged]
    oc_stop("Lines ", paste(utils::head(lines, 10), collapse = ", "),
            " do not have as many fields as the header.")
  }
  df <- utils::read.csv(text = text, sep = sep, quote = "\"", colClasses = "character",
                        na.strings = character(0), check.names = FALSE,
                        strip.white = FALSE, blank.lines.skip = FALSE, comment.char = "",
                        fill = FALSE, encoding = "UTF-8", stringsAsFactors = FALSE)
  if (nrow(df) != length(data_lines)) {
    oc_stop("The file has rows that span more than one line.")
  }
  if (any(names(df) %in% oc_fixed)) oc_stop("The source uses a reserved column name.")
  df <- cbind(stats::setNames(data.frame(as.character(data_lines), stringsAsFactors = FALSE),
                              oc_line), df)
  df
}

oc_lines <- function(df, which) I(as.integer(df[[oc_line]][which]))

oc_need <- function(df, cols) {
  missing <- setdiff(cols, names(df))
  if (length(missing) > 0L) oc_stop("No column ", missing[1], " at this point.")
}

oc_where <- function(df, w) {
  if (!is.null(w$all)) {
    out <- rep(TRUE, nrow(df))
    for (x in w$all) out <- out & oc_where(df, x)
    return(out)
  }
  oc_need(df, w$column)
  v <- df[[w$column]]
  if (!is.null(w$equals)) return(v == w$equals)
  if (!is.null(w$values)) return(v %in% w$values)
  if (isTRUE(w$is_blank)) return(v %in% oc_missing)
  oc_stop("Unknown row condition.")
}

oc_clean_name <- function(x) {
  x <- chartr("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz", x)
  x <- gsub("[^a-z0-9]+", "_", x, perl = TRUE)
  x <- gsub("^_+|_+$", "", x, perl = TRUE)
  if (!nzchar(x)) x <- "x"
  if (grepl("^[0-9]", x)) x <- paste0("x", x)
  x
}

oc_clean_names <- function(names) {
  out <- character(0)
  seen <- list()
  for (name in names) {
    if (name %in% oc_fixed) { out <- c(out, name); next }
    base <- oc_clean_name(name)
    candidate <- base
    n <- if (is.null(seen[[base]])) 1L else seen[[base]]
    while (candidate %in% out || candidate %in% oc_fixed) {
      n <- n + 1L
      candidate <- paste0(base, "_", n)
    }
    seen[[base]] <- n
    out <- c(out, candidate)
  }
  out
}

# --- Steps: each takes the data and returns list(df, drop, reason, changed, issues, dupes)

oc_result <- function(df, drop = NULL, reason = NULL, changed = 0L, issues = list(),
                      dupes = list()) {
  list(df = df, drop = drop, reason = reason, changed = as.integer(changed),
       issues = issues, dupes = dupes)
}

op_clean_names <- function(df) {
  old <- names(df)
  names(df) <- oc_clean_names(old)
  oc_result(df)
}

op_rename <- function(df, from, to) {
  oc_need(df, from)
  if (to %in% names(df)) oc_stop("There is already a column ", to, ".")
  names(df)[names(df) == from] <- to
  oc_result(df)
}

op_trim <- function(df, columns) {
  oc_need(df, columns)
  changed <- 0L
  for (col in columns) {
    v <- df[[col]]
    new <- trimws(v, which = "both", whitespace = "[ \t\r\n]")
    changed <- changed + sum(new != v)
    df[[col]] <- new
  }
  oc_result(df, changed = changed)
}

op_parse_type <- function(df, column, type, decimal_mark) {
  oc_need(df, column)
  v <- df[[column]]
  new <- v
  if (decimal_mark == ",") {
    comma <- grepl("^[+-]?[0-9]*,[0-9]+([eE][+-]?[0-9]+)?$", v, perl = TRUE)
    new[comma] <- sub(",", ".", v[comma], fixed = TRUE)
  }
  pattern <- if (type == "integer") oc_integer_re else oc_number_re
  bad <- !(new %in% oc_missing) & !grepl(pattern, new, perl = TRUE)
  df[[column]] <- new
  issues <- list()
  if (any(bad)) issues <- list(list(kind = "not_parsed", column = column, lines = oc_lines(df, bad)))
  oc_result(df, changed = sum(new != v), issues = issues)
}

op_recode <- function(df, column, from, to) {
  oc_need(df, column)
  v <- df[[column]]
  idx <- match(v, from)
  hit <- !is.na(idx)
  new <- v
  new[hit] <- to[idx[hit]]
  df[[column]] <- new
  oc_result(df, changed = sum(new != v))
}

op_derive <- function(df, target, fn, from = NULL, sep = NULL, value = NULL) {
  if (target %in% names(df)) oc_stop("There is already a column ", target, ".")
  out <- switch(fn,
    copy = { oc_need(df, from); df[[from]] },
    concat = { oc_need(df, from); do.call(paste, c(unname(as.list(df[from])), sep = sep)) },
    constant = rep(value, nrow(df)),
    oc_stop("Unknown derive function.")
  )
  df[[target]] <- out
  oc_result(df)
}

# ISO 8601 date-times: YYYY-MM-DDTHH:MM[:SS[.fff]] with an optional Z or +HH:MM. A value
# without a time, or a pair where only one side names a zone, is not derived: the row
# keeps an empty value and is reported, never dropped (ADR 0005 Q6).
oc_dt_re <- "^([0-9]{4}-[0-9]{2}-[0-9]{2})[T ]([0-9]{2}:[0-9]{2}(:[0-9]{2}(\\.[0-9]+)?)?)(Z|[+-][0-9]{2}:?[0-9]{2})?$"

oc_parse_dt <- function(x) {
  ok <- grepl(oc_dt_re, x, perl = TRUE)
  date <- sub(oc_dt_re, "\\1", x, perl = TRUE)
  time <- sub(oc_dt_re, "\\2", x, perl = TRUE)
  zone <- ifelse(ok, sub(oc_dt_re, "\\5", x, perl = TRUE), "")
  time[ok & nchar(time) == 5L] <- paste0(time[ok & nchar(time) == 5L], ":00")
  secs <- rep(NA_real_, length(x))
  stamp <- as.POSIXct(paste(date[ok], time[ok]), format = "%Y-%m-%d %H:%M:%OS", tz = "UTC")
  secs[ok] <- as.numeric(stamp)
  offset <- rep(0, length(x))
  zoned <- ok & nzchar(zone) & zone != "Z"
  if (any(zoned)) {
    z <- gsub(":", "", zone[zoned])
    sign <- ifelse(substr(z, 1, 1) == "-", -1, 1)
    offset[zoned] <- sign * (as.numeric(substr(z, 2, 3)) * 3600 + as.numeric(substr(z, 4, 5)) * 60)
  }
  list(secs = secs - offset, zoned = ok & nzchar(zone), ok = ok & !is.na(secs))
}

op_derive_elapsed_time <- function(df, target, from, reference, unit, time_zone) {
  oc_need(df, c(from, reference))
  if (target %in% names(df)) oc_stop("There is already a column ", target, ".")
  a <- oc_parse_dt(df[[from]])
  b <- oc_parse_dt(df[[reference]])
  ok <- a$ok & b$ok & (a$zoned == b$zoned)
  out <- rep("", nrow(df))
  out[ok] <- as.character(round((a$secs[ok] - b$secs[ok]) / oc_time[[unit]], 10))
  df[[target]] <- out
  issues <- list()
  if (any(!ok)) issues <- list(list(kind = "time_derivation_failed", column = target,
                                    lines = oc_lines(df, !ok)))
  oc_result(df, issues = issues)
}

op_convert_units <- function(df, value_column, unit_column, from, to) {
  oc_need(df, c(value_column, unit_column))
  table <- if (from %in% names(oc_conc)) oc_conc else oc_amount
  if (!(from %in% names(table) && to %in% names(table))) oc_stop("Unknown unit conversion.")
  factor <- table[[from]] / table[[to]]
  rows <- df[[unit_column]] == from
  v <- df[[value_column]]
  numeric <- grepl(oc_number_re, v, perl = TRUE)
  convert <- rows & numeric
  blank <- rows & v %in% oc_missing
  v[convert] <- as.character(as.numeric(v[convert]) * factor)
  df[[value_column]] <- v
  u <- df[[unit_column]]
  u[convert | blank] <- to
  df[[unit_column]] <- u
  bad <- rows & !numeric & !blank
  issues <- list()
  if (any(bad)) issues <- list(list(kind = "not_converted", column = value_column,
                                    lines = oc_lines(df, bad)))
  oc_result(df, changed = sum(convert) + sum(blank), issues = issues)
}

op_flag <- function(df, target, column, values, flags) {
  oc_need(df, column)
  if (target %in% names(df)) oc_stop("There is already a column ", target, ".")
  idx <- match(df[[column]], values)
  out <- ifelse(is.na(idx), "", ifelse(flags[idx], "TRUE", "FALSE"))
  df[[target]] <- out
  issues <- list()
  if (any(is.na(idx))) issues <- list(list(kind = "flag_value_unmapped", column = column,
                                           lines = oc_lines(df, is.na(idx))))
  oc_result(df, issues = issues)
}

op_exclude <- function(df, where, reason) {
  if (!(oc_excluded %in% names(df))) df[[oc_excluded]] <- rep("FALSE", nrow(df))
  if (!(oc_reason %in% names(df))) df[[oc_reason]] <- rep("", nrow(df))
  hit <- oc_where(df, where)
  old <- df[[oc_reason]]
  df[[oc_excluded]][hit] <- "TRUE"
  df[[oc_reason]][hit] <- ifelse(nzchar(old[hit]), paste0(old[hit], "; ", reason), reason)
  issues <- list(list(kind = "excluded", lines = oc_lines(df, hit)))
  oc_result(df, changed = sum(hit), issues = issues)
}

op_filter_rows <- function(df, where, reason) {
  hit <- oc_where(df, where)
  oc_result(df, drop = hit, reason = reason)
}

op_report_dupes <- function(df, columns) {
  oc_need(df, columns)
  key <- do.call(paste, c(unname(as.list(df[columns])), sep = "\u001f"))
  groups <- split(as.integer(df[[oc_line]]), key)
  groups <- unname(groups[vapply(groups, length, integer(1)) > 1L])
  oc_result(df, dupes = lapply(groups, I))
}

# --- Running a recipe ---------------------------------------------------------------

oc_run <- function(input, output, sep, sample_rows, recipe_id, recipe_sha256, steps) {
  dir.create(output, showWarnings = FALSE, recursive = TRUE)
  df <- oc_read(input, sep, sample_rows)
  source_rows <- nrow(df)
  rejected <- data.frame(line = integer(0), step = integer(0), reason = character(0))
  report <- list()
  for (i in seq_along(steps)) {
    s <- steps[[i]]
    rows_in <- nrow(df)
    cols_in <- names(df)
    cat("OPNCAFYR-PROGRESS: Step ", i, " of ", length(steps), ": ", s$op, "\n", sep = "")
    res <- do.call(get(paste0("op_", s$op)), c(list(df), s$args))
    out <- res$df
    n_drop <- 0L
    if (!is.null(res$drop) && any(res$drop)) {
      n_drop <- sum(res$drop)
      rejected <- rbind(rejected, data.frame(line = as.integer(out[[oc_line]][res$drop]),
                                             step = i, reason = res$reason))
      out <- out[!res$drop, , drop = FALSE]
    }
    rownames(out) <- NULL
    df <- out
    report[[i]] <- list(
      index = i, op = s$op, note = s$note,
      rows_in = rows_in, rows_out = nrow(df), rows_rejected = n_drop,
      cells_changed = res$changed,
      columns_added = I(setdiff(names(df), cols_in)),
      columns_removed = I(setdiff(cols_in, names(df))),
      issues = res$issues, dupes = res$dupes
    )
  }
  utils::write.csv(df, file.path(output, "cleaned.csv"), row.names = FALSE, na = "",
                   fileEncoding = "UTF-8", eol = "\n")
  utils::write.csv(rejected, file.path(output, "rejected.csv"), row.names = FALSE,
                   fileEncoding = "UTF-8", eol = "\n")
  summary <- list(
    recipe_id = recipe_id, recipe_sha256 = recipe_sha256, sample_rows = sample_rows,
    source_rows = source_rows, rows_out = nrow(df), rows_rejected = nrow(rejected),
    columns = I(names(df)), steps = report
  )
  jsonlite::write_json(summary, file.path(output, "steps.json"), auto_unbox = TRUE,
                       pretty = TRUE, null = "null", digits = NA)
  cat("OPNCAFYR-PROGRESS: Cleaned ", nrow(df), " of ", source_rows, " rows\n", sep = "")
  invisible(df)
}
