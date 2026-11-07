# Excerpt of makecoolplots r/make_fixtures.R (jprybylski/makecoolplots @ 4e4b8d3, release 0.1.1)
# that builds tests/fixtures/adpc.csv from xgxr::sad. Kept for provenance (#23). It needs
# the xgxr package, which is not in the launcher's R lock, so it is not run here: the CSV
# is copied byte for byte from makecoolplots/fixtures/adpc.csv. Do not hand-edit it.

local({
  out_dir <- "fixtures"
  # Write with "\n" line endings on every platform (binary connection), no row names,
  # NA as the literal "NA", and R's default 15-significant-digit number formatting.
  write_fixture <- function(df, name, quote = TRUE, na = "NA") {
    path <- file.path(out_dir, name)
    con <- file(path, open = "wb")
    on.exit(close(con))
    utils::write.table(
      df, con,
      sep = ",", quote = quote, na = na, row.names = FALSE, col.names = TRUE,
      eol = "\n", fileEncoding = "UTF-8"
    )
    message(sprintf("wrote %-22s %6d rows x %2d cols", path, nrow(df), ncol(df)))
  }

  sad_env <- new.env()
  utils::data(list = "sad", package = "xgxr", envir = sad_env)
  sad <- as.data.frame(sad_env$sad)

  sad_subj <- sad[!duplicated(sad$ID), ]
  sad_subj <- sad_subj[order(sad_subj$ID), ]
  usubjid <- function(id) sprintf("XGXSAD-%03d", id)

  # adpc.csv: a BDS dataset of the PK samples (CMT 2): one parameter, PKCONC. AVAL is
  # blank for samples below the limit of quantification (CENS 1, AVALC "BLQ", BLQFL "Y")
  # and for missing samples; ANL01FL marks the records with a value.
  pk_all <- sad[sad$CMT == 2 & sad$EVID == 0, ]
  pk_all <- pk_all[order(pk_all$ID, pk_all$TIME), ]
  quant <- !is.na(pk_all$LIDV) & pk_all$CENS == 0
  adpc <- data.frame(
    STUDYID = "XGXSAD",
    USUBJID = usubjid(pk_all$ID),
    PARAMCD = "PKCONC",
    PARAM = "Drug concentration (ng/mL)",
    AVAL = ifelse(quant, pk_all$LIDV, NA),
    AVALC = ifelse(quant, format(pk_all$LIDV, trim = TRUE, drop0trailing = TRUE),
                   ifelse(pk_all$CENS == 1, "BLQ", "")),
    AVALU = "ng/mL",
    AFRLT = pk_all$TIME,
    NFRLT = pk_all$NOMTIME,
    BLQFL = ifelse(pk_all$CENS == 1, "Y", ""),
    ANL01FL = ifelse(is.na(pk_all$LIDV), "", "Y"),
    TRT01A = pk_all$TRTACT,
    stringsAsFactors = FALSE
  )
  write_fixture(adpc, "adpc.csv", na = "")
})
