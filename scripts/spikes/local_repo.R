# Build the local CRAN-like repository indexes for the private library (install spikes).
#
#   Rscript --vanilla local_repo.R <bin/windows/contrib/4.6 or bin/macosx/.../4.6> <src/contrib> <win.binary|mac.binary>
#
# renv 1.2/1.3 reads each package's DESCRIPTION from the *source* index while it plans
# the install; a package missing there is looked up on crandb.r-pkg.org (a network call
# that also sends the package name, e.g. opnca, to a third party). So the source index
# lists every package: real source tarballs where present, otherwise the binary entry.
a <- commandArgs(TRUE); bin <- a[[1]]; src <- a[[2]]
tools::write_PACKAGES(bin, type = if (length(a) >= 3) a[[3]] else "win.binary")
tools::write_PACKAGES(src, type = "source")
b <- read.dcf(file.path(bin, "PACKAGES")); s <- read.dcf(file.path(src, "PACKAGES"))
cols <- union(colnames(s), colnames(b))
pad <- function(m) { out <- matrix(NA_character_, nrow(m), length(cols), dimnames = list(NULL, cols)); out[, colnames(m)] <- m; out }
all <- rbind(pad(s), pad(b[!b[, "Package"] %in% s[, "Package"], , drop = FALSE]))
write.dcf(all, file.path(src, "PACKAGES"))
# R reads PACKAGES.rds (then .gz) before PACKAGES: rewrite them from the merged index.
unlink(file.path(src, c("PACKAGES.rds", "PACKAGES.gz")))
gz <- gzfile(file.path(src, "PACKAGES.gz"), "wt"); write.dcf(all, gz); close(gz)
cat(nrow(all), "packages in the source index\n")
