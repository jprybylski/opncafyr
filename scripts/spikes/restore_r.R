# Restore runtime/r/renv.lock into a private library from a local CRAN-like repository
# (install spikes #6, #7). Same path online and offline: the installer first fetches the
# pinned binaries (runtime/r/binaries.toml) into the local repository and checks them.
#
#   Rscript --vanilla restore_r.R <renv.lock> <local repo dir> <cellar dir> <library dir>
#
# Set RENV_PATHS_ROOT to an app-owned directory so renv's cache stays out of the user's
# profile defaults.
args <- commandArgs(trailingOnly = TRUE)
stopifnot(length(args) == 4L)
lockfile <- normalizePath(args[[1]])
repo <- paste0("file:///", sub("^/", "", normalizePath(args[[2]], winslash = "/")))
Sys.setenv(RENV_PATHS_CELLAR = normalizePath(args[[3]]))
lib <- normalizePath(args[[4]], mustWork = FALSE)
dir.create(lib, recursive = TRUE, showWarnings = FALSE)
.libPaths(lib)
options(repos = c(P3M = repo), renv.config.repos.override = repo,
        install.packages.compile.from.source = "never")
t0 <- Sys.time()
install.packages("renv", lib = lib, repos = repo, type = "binary", quiet = TRUE)
library(renv, lib.loc = lib, warn.conflicts = FALSE)
renv::restore(lockfile = lockfile, library = lib, prompt = FALSE)
cat(sprintf("restore: %.0f s\n", as.numeric(difftime(Sys.time(), t0, units = "secs"))))
lock <- jsonlite::read_json(lockfile)$Packages
bad <- character()
for (p in names(lock)) {
  v <- tryCatch(packageDescription(p, lib.loc = lib, fields = "Version"), warning = function(w) NA)
  if (!identical(v, lock[[p]]$Version)) bad <- c(bad, sprintf("%s: %s, locked %s", p, v, lock[[p]]$Version))
}
cat(sprintf("locked: %d, wrong or missing in the private library: %d\n", length(lock), length(bad)))
if (length(bad)) { writeLines(bad); quit(status = 1) }
