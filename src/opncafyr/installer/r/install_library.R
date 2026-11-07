# Install the app-private R library from local, verified files only (#19).
#
#   Rscript --vanilla install_library.R <lib> <repo> <contrib> <cellar> <lock.tsv> <opnca file>
#
# <opnca file> is "-" for `repair --build-from-source` (#34): opnca-r is then built after
# this script, so it is skipped here and left out of the version check.
# <repo>/<contrib> holds the pinned binaries (runtime/r/binaries.toml), <cellar> the opnca-r
# binary (named opnca_<version>.<ext>) and the quartifyr, reportifyr and pyro source
# tarballs, <lock.tsv> the name, version and source of every package in runtime/r/renv.lock.
# Base R only: no renv (its restore needs the Command Line Tools on macOS and goes online
# for packages missing from an index), no compilers (binaries and pure-R sources only), and
# the only repository is the local one, so a missing file fails instead of downloading.
args <- commandArgs(trailingOnly = TRUE)
if (length(args) != 6L) stop("usage: install_library.R <lib> <repo> <contrib> <cellar> <lock.tsv> <opnca>")
lib <- normalizePath(args[[1]], winslash = "/", mustWork = TRUE)
repo <- normalizePath(args[[2]], winslash = "/", mustWork = TRUE)
contrib <- args[[3]]
cellar <- normalizePath(args[[4]], winslash = "/", mustWork = TRUE)
lock <- utils::read.delim(args[[5]], colClasses = "character")
opnca_file <- if (identical(args[[6]], "-")) NA_character_ else file.path(cellar, args[[6]])

.libPaths(lib)
repo_url <- paste0("file:///", sub("^/", "", repo))
type <- .Platform$pkgType
expected <- sub("^REPO/", "", utils::contrib.url("REPO", type = type))
if (!identical(expected, contrib)) {
  stop(sprintf("this R expects binaries in %s, the pins provide %s", expected, contrib))
}
index_type <- if (.Platform$OS.type == "windows") "win.binary" else "mac.binary"
tools::write_PACKAGES(file.path(repo, contrib), type = index_type)
options(
  repos = c(local = repo_url),
  install.packages.compile.from.source = "never",
  install.packages.check.source = "no"
)

started <- Sys.time()
from_repo <- lock$name[lock$source == "Repository"]
cat(sprintf("installing %d binary packages\n", length(from_repo)))
utils::install.packages(from_repo, lib = lib, repos = repo_url, type = "binary",
                        dependencies = FALSE, quiet = TRUE)

if (!is.na(opnca_file)) {
  cat("installing opnca-r from its verified binary\n")
  utils::install.packages(opnca_file, lib = lib, repos = NULL, type = "binary", quiet = TRUE)
}

# The pure-R tool packages, each after the cellar packages it depends on.
tarballs <- list.files(cellar, pattern = "\\.tar\\.gz$", full.names = TRUE)
pkg_of <- function(f) sub("_.*$", "", basename(f))
deps_of <- function(f) {
  tmp <- tempfile()
  on.exit(unlink(tmp, recursive = TRUE))
  p <- pkg_of(f)
  utils::untar(f, files = file.path(p, "DESCRIPTION"), exdir = tmp)
  d <- read.dcf(file.path(tmp, p, "DESCRIPTION"))
  fields <- intersect(c("Depends", "Imports", "LinkingTo"), colnames(d))
  tools:::.extract_dependency_package_names(paste(d[, fields], collapse = ","))
}
pending <- tarballs
while (length(pending)) {
  names_pending <- vapply(pending, pkg_of, "")
  ready <- Filter(function(f) !any(deps_of(f) %in% setdiff(names_pending, pkg_of(f))), pending)
  if (!length(ready)) stop("cellar packages depend on each other in a cycle")
  for (f in ready) {
    cat("installing", pkg_of(f), "from source (pure R)\n")
    utils::install.packages(f, lib = lib, repos = NULL, type = "source", quiet = TRUE)
  }
  pending <- setdiff(pending, ready)
}

installed <- utils::installed.packages(lib.loc = lib)[, "Version"]
want <- stats::setNames(lock$version, lock$name)
if (is.na(opnca_file)) want <- want[names(want) != "opnca"]
bad <- names(want)[is.na(installed[names(want)]) | installed[names(want)] != want]
cat(sprintf("locked: %d, installed: %d, missing or wrong version: %d (%.0f s)\n",
            length(want), length(installed), length(bad),
            as.numeric(difftime(Sys.time(), started, units = "secs"))))
if (length(bad)) {
  writeLines(sprintf("  %s: installed %s, locked %s", bad, installed[bad], want[bad]))
  quit(status = 1)
}
