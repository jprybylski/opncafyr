# Fixed R programs

R code the launcher runs lives here as fixed, reviewed files. Every one is run through
`core/rscript.py` as

    Rscript --vanilla <script>

with the pinned runtime's `Rscript` and app-private library (`R_LIBS`). `--vanilla` keeps
any `.Rprofile`, `.Renviron` or saved workspace in the study folder from running, which
also keeps R offline (docs/offline.md R12, R15). Arguments reach a program as argv or
environment variables; recipe text reaches generated code only as escaped string
literals (`workflows/nca/clean_compile.r_string`). Workspace files supply data and options
only. These programs use base R and jsonlite, nothing else.

- `nca_clean_lib.R`: the cleaning library (#22). `clean_compile.py` inlines it into each
  generated `clean.R`, followed by one call per recipe step.
- `nca_format_check.R`: `opnca::check_data_format()` on one analyte selection (#24); writes
  roles, column names and opnca-r's issue texts, never a value.
