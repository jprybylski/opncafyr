# Check that runtime/r/renv.lock restores into an isolated library (#5).
#
#   RENV_PATHS_ROOT=<tmp> RENV_PATHS_CELLAR=<cellar> TEST_LIB=<empty dir> \
#   TEST_BASELIB=<empty dir> TEST_REPO=<local CRAN-like repo> \
#     Rscript --vanilla scripts/check_r_restore.R        # run in a dir holding renv.lock
#
# <repo> is a local CRAN-like repository built from the files in runtime/r/binaries.toml
# plus the four tool packages (bin/.../contrib/4.6 and src/contrib). Its *source* index
# must list all 130 packages, using the binary entry where there is no source tarball:
# renv plans the install from the source index and looks anything missing up on
# crandb.r-pkg.org. <cellar> holds opnca_0.3.3.<tgz|zip> and the quartifyr, reportifyr
# and pyro source tarballs. Run it with the network blocked (macOS: sandbox-exec with a
# deny network-outbound profile) to show the restore needs nothing but those files.
# docs/dependency-graph.md has the steps.
#
#
# A clean per-user R has only base and recommended packages in R_HOME/library; on a dev
# machine that library holds many more, so present R with a base+recommended-only copy.
lib <- Sys.getenv("TEST_LIB")
baselib <- Sys.getenv("TEST_BASELIB")
repo <- paste0("file://", Sys.getenv("TEST_REPO"))
ip <- installed.packages(.Library, priority = c("base", "recommended"))
for (p in rownames(ip)) file.symlink(file.path(.Library, p), file.path(baselib, p))
unlockBinding(".Library", baseenv()); assign(".Library", baselib, envir = baseenv())
.libPaths(lib)
stopifnot(identical(.libPaths(), c(normalizePath(lib), baselib)))
install.packages("renv", lib = lib, repos = repo, type = "binary", quiet = TRUE)
library(renv, lib.loc = lib, warn.conflicts = FALSE)
options(repos = c(P3M = repo), renv.config.repos.override = repo)
t0 <- Sys.time()
renv::restore(lockfile = "renv.lock", library = lib, prompt = FALSE)
cat(sprintf("restore took %.0f s\n", as.numeric(difftime(Sys.time(), t0, units = "secs"))))
lock <- jsonlite::read_json("renv.lock")$Packages
bad <- character()
for (p in names(lock)) {
  d <- tryCatch(packageDescription(p, lib.loc = lib, fields = "Version"), warning = function(w) NA)
  if (!identical(d, lock[[p]]$Version)) bad <- c(bad, sprintf("%s: %s != %s", p, d, lock[[p]]$Version))
}
cat("packages in lock:", length(lock), " mismatched or missing in private lib:", length(bad), "\n")
if (length(bad)) print(bad)
for (p in c("opnca", "quartifyr", "reportifyr", "plumber2", "flextable", "officer")) {
  suppressPackageStartupMessages(library(p, character.only = TRUE, lib.loc = lib))
  cat(p, "loaded from private lib:", dirname(find.package(p)) == normalizePath(lib), "\n")
}
