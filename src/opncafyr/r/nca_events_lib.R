# opncafyr event-record stage (#58, ADR 0005 Q9). Fixed, reviewed code: the recipe's
# events block supplies data only (column names, code tables and the user's choices).
#
# It runs after the cleaning steps, on every row they kept, and puts each record in
# exactly one partition:
#   selected            a PK observation in the chosen dosing interval (or kept predose)
#   predose_set_aside   a PK observation before the first dose, set aside by choice
#   outside_interval    a PK observation in another dose's interval
#   dose                a dose record (used for a selected observation, or not)
#   other_endpoint      an observation of an endpoint that is not PK
#   other_record        a record whose kind is "other"
#   rejected            a PK observation with no dose, removed by the "reject" choice
#                       (written to rejected.csv with the step and reason)
#   unresolved          anything the confirmed tables and choices don't settle, with a
#                       check id; it blocks every run until a recipe change settles it
# Only selected rows go on, to prepared.csv, with new columns for the dose, its unit, route
# and time, the time after it and the dose's source lines. Raw columns are never changed,
# no row is made up, no zero is written, a route is never filled in and nothing is
# chosen for the user. Uses base R and jsonlite only. Messages give counts, never values.

oc_event_columns <- c("opncafyr_dose", "opncafyr_dose_unit", "opncafyr_route",
                      "opncafyr_dose_time", "opncafyr_time_after_dose",
                      "opncafyr_dose_number", "opncafyr_dose_lines")
oc_partitions <- c("selected", "predose_set_aside", "outside_interval", "dose",
                   "other_endpoint", "other_record", "rejected", "unresolved")
oc_unmatched_reason <- "No dose record for this observation's dosing interval (event records: reject unmatched)."

oc_num <- function(x) {
  out <- rep(NA_real_, length(x))
  ok <- grepl(oc_number_re, x, perl = TRUE)
  out[ok] <- as.numeric(x[ok])
  out
}

# One number as plain decimal text: 1e5 is "100000", 0.1 + 0.2 is "0.3".
oc_fmt <- function(x) {
  vapply(x, function(v) format(round(v, 10), scientific = FALSE, digits = 15, trim = TRUE),
         character(1), USE.NAMES = FALSE)
}

oc_col <- function(df, name) if (is.null(name) || !nzchar(name)) NULL else df[[name]]

oc_prepare_events <- function(df, output, events, n_steps, events_sha256) {
  if (any(oc_event_columns %in% names(df))) oc_stop("The data already has a prepared event column.")
  cols <- events$columns
  for (name in unlist(cols)) oc_need(df, name)
  ch <- events$choices
  n <- nrow(df)
  line <- as.integer(df[[oc_line]])
  part <- rep("", n)
  cat_ <- rep("", n)
  used <- rep(FALSE, n)
  unresolved <- function(which, check) {
    hit <- which & part == ""
    part[hit] <<- "unresolved"
    cat_[hit] <<- check
  }

  excluded <- if (oc_excluded %in% names(df)) df[[oc_excluded]] == "TRUE" else rep(FALSE, n)
  kind <- events$kinds$kinds[match(df[[cols$record_kind]], events$kinds$values)]
  unresolved(is.na(kind), "event_kind_unknown")
  kind[is.na(kind)] <- ""
  mdv <- rep(NA, n)
  if (!is.null(oc_col(df, cols$mdv))) {
    idx <- match(df[[cols$mdv]], events$mdv$values)
    unresolved(is.na(idx), "event_mdv_unmapped")
    mdv <- events$mdv$flags[idx]
  }
  dv <- oc_col(df, cols$dv)
  dv_number <- if (is.null(dv)) rep(NA_real_, n) else oc_num(dv)
  time <- oc_num(df[[cols$time]])
  time_text <- df[[cols$time]]
  amount <- oc_num(df[[cols$amount]])
  time_unit <- oc_col(df, cols$time_unit)
  dose_unit <- oc_col(df, cols$dose_unit)
  route <- df[[cols$route]]

  part[part == "" & kind == "other"] <- "other_record"

  # Observations: the endpoint table decides PK or not.
  obs <- part == "" & kind == "observation"
  endpoint <- events$endpoints$kinds[match(df[[cols$endpoint]], events$endpoints$values)]
  unresolved(obs & is.na(endpoint), "event_endpoint_unknown")
  part[part == "" & obs & endpoint %in% "other"] <- "other_endpoint"
  pk <- part == "" & obs
  unresolved(pk & !excluded & mdv %in% TRUE & !is.na(dv_number), "event_flag_conflict")
  unresolved(pk & is.na(time), "event_time_not_numeric")

  # Dose records: each problem blocks; the fix is a visible recipe step.
  dose <- part == "" & kind == "dose"
  unresolved(dose & excluded, "event_excluded_dose")
  unresolved(dose & is.na(time), "event_time_not_numeric")
  unresolved(dose & !(amount > 0) %in% TRUE, "event_dose_amount_invalid")
  dv_bad <- if (is.null(dv)) rep(FALSE, n) else !(dv %in% oc_missing) & !(dv_number %in% 0)
  unresolved(dose & (mdv %in% FALSE | dv_bad), "event_flag_conflict")
  bad_dose <- dose & part == "unresolved"
  part[part == "" & dose] <- "dose"

  # Groups: subject, and occasion when one is named.
  group <- df[[cols$subject]]
  if (!is.null(oc_col(df, cols$occasion))) group <- paste(group, df[[cols$occasion]], sep = "\u001f")

  new <- list(dose = rep("", n), unit = rep("", n), route = rep("", n), time = rep("", n),
              tad = rep("", n), number = rep("", n), lines = rep("", n))
  rejected_lines <- integer(0)
  pk <- part == "" & obs
  for (g in unique(group[pk | part == "dose" | bad_dose])) {
    in_g <- group == g
    o <- which(in_g & part == "" & obs)
    if (length(o) == 0L) next
    if (any(in_g & bad_dose)) {
      part[o] <- "unresolved"; cat_[o] <- "event_dose_unresolved"; next
    }
    d <- which(in_g & part == "dose")
    d <- d[order(time[d], line[d])]
    # Dose events: records at one time are one event only by the "sum" choice.
    times <- unique(time[d])
    ev <- lapply(times, function(t) d[time[d] == t])
    multi <- vapply(ev, length, integer(1)) > 1L
    if (any(multi)) {
      split_lines <- unlist(ev[multi])
      differ <- vapply(ev[multi], function(r) {
        length(unique(route[r])) > 1L ||
          (!is.null(dose_unit) && length(unique(dose_unit[r])) > 1L) ||
          (!is.null(time_unit) && length(unique(time_unit[r])) > 1L)
      }, logical(1))
      if (!identical(ch$same_time_doses, "sum")) {
        part[split_lines] <- "unresolved"; cat_[split_lines] <- "event_same_time_doses"
      } else if (any(differ)) {
        r <- unlist(ev[multi][differ])
        part[r] <- "unresolved"; cat_[r] <- "event_same_time_doses_differ"
      }
      if (any(part[split_lines] == "unresolved")) {
        part[o] <- "unresolved"; cat_[o] <- "event_dose_unresolved"; next
      }
    }
    nd <- length(ev)
    k <- if (nd == 0L) NA_integer_
         else if (nd == 1L && !identical(ch$dose_interval, "number")) 1L
         else switch(if (is.null(ch$dose_interval)) "none" else ch$dose_interval,
                     first = 1L, last = nd, number = as.integer(ch$dose_number), NA_integer_)
    if (nd > 1L && is.na(k)) {
      part[o] <- "unresolved"; cat_[o] <- "event_multiple_doses"; next
    }
    if (is.na(k) || k > nd) {
      if (identical(ch$unmatched, "reject")) {
        part[o] <- "rejected"; rejected_lines <- c(rejected_lines, line[o])
      } else {
        part[o] <- "unresolved"; cat_[o] <- "event_unmatched"
      }
      next
    }
    start <- times[k]
    end <- if (k < nd) times[k + 1L] else Inf
    r <- ev[[k]]
    t <- time[o]
    # A sample exactly at a dose time belongs to one interval or the other; ask only when
    # the answer changes the result (at the first dose with predose "keep" it doesn't).
    at <- t == end | (t == start & !(k == 1L & identical(ch$predose, "keep")))
    if (any(at) && is.null(ch$observation_at_dose_time)) {
      part[o[at]] <- "unresolved"; cat_[o[at]] <- "event_at_dose_time"
    }
    before <- identical(ch$observation_at_dose_time, "before")
    inside <- (t > start | (t == start & !before)) & (t < end | (t == end & before))
    early <- t < start | (t == start & before)
    late <- !inside & !early
    for (i in seq_along(o)) {
      j <- o[i]
      if (part[j] != "") next
      if (late[i]) { part[j] <- "outside_interval"; next }
      if (early[i] && k > 1L) { part[j] <- "outside_interval"; next }
      if (early[i]) {
        if (identical(ch$predose, "set_aside")) { part[j] <- "predose_set_aside"; next }
        if (!identical(ch$predose, "keep")) { part[j] <- "unresolved"; cat_[j] <- "event_predose"; next }
      }
      if (!is.null(time_unit) && time_unit[j] != time_unit[r[1]]) {
        part[j] <- "unresolved"; cat_[j] <- "event_time_units_differ"; next
      }
      part[j] <- "selected"
      new$dose[j] <- if (length(r) == 1L) df[[cols$amount]][r] else oc_fmt(sum(amount[r]))
      new$unit[j] <- if (is.null(dose_unit)) "" else dose_unit[r[1]]
      new$route[j] <- route[r[1]]
      new$time[j] <- time_text[r[1]]
      new$tad[j] <- oc_fmt(t[i] - start)
      new$number[j] <- as.character(k)
      new$lines[j] <- paste(line[r], collapse = ";")
      used[r] <- TRUE
    }
  }
  # Observations in groups with no dose record at all.
  rest <- part == "" & obs
  if (any(rest)) {
    if (identical(ch$unmatched, "reject")) {
      part[rest] <- "rejected"; rejected_lines <- c(rejected_lines, line[rest])
    } else {
      part[rest] <- "unresolved"; cat_[rest] <- "event_unmatched"
    }
  }
  if (any(part == "")) oc_stop("Event records: a record was left without a partition.")

  sel <- part == "selected"
  out <- df[sel, , drop = FALSE]
  out[["opncafyr_dose"]] <- new$dose[sel]
  out[["opncafyr_dose_unit"]] <- new$unit[sel]
  out[["opncafyr_route"]] <- new$route[sel]
  out[["opncafyr_dose_time"]] <- new$time[sel]
  out[["opncafyr_time_after_dose"]] <- new$tad[sel]
  out[["opncafyr_dose_number"]] <- new$number[sel]
  out[["opncafyr_dose_lines"]] <- new$lines[sel]
  rownames(out) <- NULL
  utils::write.csv(out, file.path(output, "prepared.csv"), row.names = FALSE, na = "",
                   fileEncoding = "UTF-8", eol = "\n")

  dose_lines <- rep("", n)
  dose_lines[sel] <- new$lines[sel]
  category <- cat_
  category[part == "dose"] <- ifelse(used[part == "dose"], "used", "unused")
  records <- data.frame(line = line, partition = part, category = category,
                        dose_lines = dose_lines, stringsAsFactors = FALSE)
  records <- records[order(records$line), , drop = FALSE]
  utils::write.csv(records, file.path(output, "records.csv"), row.names = FALSE,
                   fileEncoding = "UTF-8", eol = "\n")

  if (length(rejected_lines) > 0L) {
    rej <- data.frame(line = sort(rejected_lines), step = n_steps + 1L,
                      reason = oc_unmatched_reason)
    utils::write.table(rej, file.path(output, "rejected.csv"), append = TRUE, sep = ",",
                       row.names = FALSE, col.names = FALSE, qmethod = "double",
                       fileEncoding = "UTF-8", eol = "\n")
  }

  counts <- lapply(oc_partitions, function(p) sum(part == p))
  names(counts) <- oc_partitions
  checks <- sort(unique(cat_[part == "unresolved"]))
  groups <- lapply(checks, function(c) list(check = c, lines = I(sort(line[cat_ == c & part == "unresolved"]))))
  summary <- list(
    events_sha256 = events_sha256, rows_in = n, partitions = counts,
    doses_used = sum(part == "dose" & used), doses_unused = sum(part == "dose" & !used),
    rows_rejected = length(rejected_lines), unresolved = groups
  )
  jsonlite::write_json(summary, file.path(output, "events.json"), auto_unbox = TRUE,
                       pretty = TRUE, null = "null", digits = NA)
  cat("OPNCAFYR-PROGRESS: Event records: ", sum(sel), " observations selected of ", n,
      " records\n", sep = "")
  invisible(out)
}
