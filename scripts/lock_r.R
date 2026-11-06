# Generate runtime/r/renv.lock for the app-private R library (#5).
#
#   Rscript --vanilla scripts/lock_r.R --snapshot 2026-11-06 --cellar <dir> [--out runtime/r/renv.lock]
#
# <dir> holds the pinned source tarballs of the packages that do not come from CRAN
# (opnca, quartifyr, reportifyr, pyro). Their DESCRIPTION files give the top-level
# dependencies; EXTRA adds what they need only as Suggests (opnca-r's web app) plus renv.
# The closure (Depends, Imports, LinkingTo) is computed against the dated Posit Package
# Manager snapshot, so the same snapshot date always gives the same lockfile. Nothing is
# installed. Base and recommended packages that ship with R are left out.

args <- commandArgs(trailingOnly = TRUE)
opt <- function(name, default = NULL) {
  i <- match(paste0("--", name), args)
  if (is.na(i)) {
    if (is.null(default)) stop("missing --", name, call. = FALSE)
    return(default)
  }
  args[[i + 1L]]
}
snapshot <- opt("snapshot")
cellar <- opt("cellar")
out <- opt("out", "runtime/r/renv.lock")
r_version <- opt("r-version", "4.6.2")

repo <- sprintf("https://packagemanager.posit.co/cran/%s", snapshot)

# Packages the tools use only through Suggests. opnca-r's opnca_app()/scripts/serve.R
# needs plumber2, httpuv, reqres, jsonlite and processx (opnca-r R/app.R).
EXTRA <- c("plumber2", "httpuv", "reqres", "jsonlite", "processx", "renv")

# The packages restored from the renv cellar (the verified files in runtime/pins.toml,
# which also records where they come from). They are locked as `Source: Cellar`: with
# `Source: GitHub`, renv 1.3 downloads them from GitHub before it looks in the cellar
# (seen in the Windows spike, #6), which fails for private opnca-r.
CELLAR <- c("opnca", "quartifyr", "reportifyr", "pyro")

read_description <- function(tarball) {
  pkg <- sub("_.*$", "", basename(tarball))
  tmp <- tempfile()
  dir.create(tmp)
  on.exit(unlink(tmp, recursive = TRUE))
  utils::untar(tarball, files = file.path(pkg, "DESCRIPTION"), exdir = tmp)
  read.dcf(file.path(tmp, pkg, "DESCRIPTION"))[1, ]
}

deps_of <- function(desc) {
  fields <- intersect(c("Depends", "Imports", "LinkingTo"), names(desc))
  if (!length(fields)) return(character())
  raw <- unlist(strsplit(desc[fields], ","))
  raw <- trimws(sub("\\(.*$", "", trimws(raw)))
  raw[nzchar(raw) & raw != "R"]
}

tarballs <- list.files(cellar, pattern = "^[A-Za-z0-9.]+_[0-9.-]+\\.tar\\.gz$", full.names = TRUE)
# Prefer the -vendored opnca tarball's DESCRIPTION; both carry the same one.
tarballs <- tarballs[!grepl("-vendored", tarballs)]
descs <- lapply(tarballs, read_description)
names(descs) <- vapply(descs, function(d) d[["Package"]], "")
missing <- setdiff(CELLAR, names(descs))
if (length(missing)) stop("cellar is missing: ", paste(missing, collapse = ", "), call. = FALSE)

ap <- utils::available.packages(repos = repo, type = "source", filters = list())
ip <- utils::installed.packages(priority = c("base", "recommended"))
base_pkgs <- rownames(ip)[ip[, "Priority"] %in% "base"]
# Recommended packages ship with R but can be updated; we lock the versions the snapshot
# has only if something needs them, and keep base packages out entirely.
top <- unique(c(unlist(lapply(descs, deps_of)), EXTRA))
top <- setdiff(top, c(names(descs), base_pkgs))
unknown <- setdiff(top, rownames(ap))
if (length(unknown)) stop("not in the snapshot: ", paste(unknown, collapse = ", "), call. = FALSE)

closure <- tools::package_dependencies(
  top, db = ap, which = c("Depends", "Imports", "LinkingTo"), recursive = TRUE
)
pkgs <- sort(setdiff(unique(c(top, unlist(closure))), base_pkgs))
recommended <- rownames(ip)[ip[, "Priority"] %in% "recommended"]

entry_repo <- function(p) {
  list(
    Package = p,
    Version = unname(ap[p, "Version"]),
    Source = "Repository",
    Repository = "P3M"
  )
}
entry_cellar <- function(p) {
  list(Package = p, Version = unname(descs[[p]][["Version"]]), Source = "Cellar")
}

packages <- c(
  lapply(stats::setNames(pkgs, pkgs), entry_repo),
  lapply(stats::setNames(CELLAR, CELLAR), entry_cellar)
)
packages <- packages[order(names(packages))]

lock <- list(
  R = list(
    Version = r_version,
    Repositories = list(list(Name = "P3M", URL = repo))
  ),
  Packages = packages
)
dir.create(dirname(out), recursive = TRUE, showWarnings = FALSE)
writeLines(jsonlite::toJSON(lock, auto_unbox = TRUE, pretty = 2), out, useBytes = TRUE)
message(sprintf(
  "%s: %d packages (%d from the snapshot, %d from the cellar; %d recommended)",
  out, length(packages), length(pkgs), length(CELLAR), length(intersect(pkgs, recommended))
))
