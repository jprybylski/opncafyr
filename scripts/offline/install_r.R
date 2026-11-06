# Offline CI (#44): restore the app-private R library from local files only.
#
#   Rscript --vanilla install_r.R <renv.lock> <rrepo dir> <cellar dir> <library dir>
#
# The installer's path (#19, #34, #36): renv::restore() against a local CRAN-like
# repository, with renv itself installed from that repository first. No repository URL
# other than the local one is configured, so a missing file fails the restore instead of
# reaching for the network.
#
# The packages that don't come from CRAN (opnca, quartifyr, reportifyr, pyro) are
# installed from the cellar folder with R CMD INSTALL, in dependency order, not through
# renv's cellar: on Linux a binary package is also a .tar.gz, so renv takes the cellar's
# source tarball for a binary, and then fell back to downloading the package from GitHub
# (traced; docs/offline.md I6). macOS (.tgz) and Windows (.zip) binaries don't have this
# ambiguity. A package missing from the cellar (opnca-r without its release token) is
# left out and reported.

args <- commandArgs(trailingOnly = TRUE)
if (length(args) != 4) {
  stop("usage: install_r.R <renv.lock> <rrepo> <cellar> <library>", call. = FALSE)
}
lockfile <- normalizePath(args[[1]], mustWork = TRUE)
repo <- normalizePath(args[[2]], mustWork = TRUE)
cellar <- normalizePath(args[[3]], mustWork = TRUE)
lib <- args[[4]]
dir.create(lib, recursive = TRUE, showWarnings = FALSE)
lib <- normalizePath(lib, mustWork = TRUE)

repo_url <- paste0("file://", repo)
options(repos = c(P3M = repo_url), renv.config.repos.override = repo_url)
Sys.setenv(
  RENV_PATHS_CELLAR = cellar,
  RENV_PATHS_ROOT = file.path(dirname(lib), "renv-root"),
  RENV_CONFIG_AUTOLOADER_ENABLED = "FALSE",
  RENV_CONFIG_UPDATES_CHECK = "FALSE"
)
.libPaths(lib)

untar_description <- function(tarball, package) {
  tmp <- tempfile()
  on.exit(unlink(tmp, recursive = TRUE))
  utils::untar(tarball, files = file.path(package, "DESCRIPTION"), exdir = tmp)
  readLines(file.path(tmp, package, "DESCRIPTION"))
}

started <- Sys.time()
utils::install.packages("renv", lib = lib, repos = repo_url, type = "source", quiet = TRUE)
library(renv, lib.loc = lib)

lock <- renv::lockfile_read(lockfile)
records <- lock$Packages
non_cran <- names(Filter(function(r) !identical(r$Source, "Repository"), records))
cellar_file <- function(p) file.path(cellar, sprintf("%s_%s.tar.gz", p, records[[p]]$Version))
excluded <- non_cran[!file.exists(vapply(non_cran, cellar_file, ""))]
if (length(excluded)) {
  message("Not in the cellar, left out: ", paste(excluded, collapse = ", "))
}

renv::restore(
  lockfile = lockfile,
  library = lib,
  exclude = non_cran,
  prompt = FALSE,
  clean = FALSE
)

# Cellar packages in dependency order: each one's dependencies are already installed.
pending <- setdiff(non_cran, excluded)
while (length(pending)) {
  ready <- Filter(function(p) {
    desc <- read.dcf(textConnection(untar_description(cellar_file(p), p)))
    deps <- tools:::.extract_dependency_package_names(
      paste(desc[, intersect(c("Depends", "Imports", "LinkingTo"), colnames(desc))], collapse = ",")
    )
    !any(deps %in% pending)
  }, pending)
  if (!length(ready)) stop("cellar packages depend on each other in a cycle", call. = FALSE)
  for (p in ready) {
    status <- system2(
      file.path(R.home("bin"), "R"),
      c("CMD", "INSTALL", "--no-test-load", paste0("--library=", shQuote(lib)), shQuote(cellar_file(p)))
    )
    if (status != 0) stop("R CMD INSTALL failed for ", p, call. = FALSE)
  }
  pending <- setdiff(pending, ready)
}

installed <- utils::installed.packages(lib.loc = lib)[, "Version"]
expected <- vapply(records[setdiff(names(records), excluded)], `[[`, "", "Version")
wrong <- expected[is.na(installed[names(expected)]) | installed[names(expected)] != expected]
if (length(wrong)) {
  stop(
    "Not restored at the locked version: ",
    paste(sprintf("%s %s", names(wrong), wrong), collapse = ", "),
    call. = FALSE
  )
}
cat(sprintf(
  "restored %d packages (%d excluded) in %.1f min\n",
  length(expected), length(excluded),
  as.numeric(difftime(Sys.time(), started, units = "mins"))
))
