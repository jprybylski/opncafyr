#!/usr/bin/env bash
# Spike #7: clean standard-user install on macOS through to rendered artifacts.
#
# Installs every pinned runtime per user under one root, with no admin rights and no
# Command Line Tools, then proves the chain: opnca-r loads and calculates, its web app
# answers /api/v1/health, deckifyr builds a PPTX and quartifyr + reportifyr render a DOCX.
# Results: docs/spikes/macos-install.md. Spike tooling only; installer v1 (#19) does
# this for real.
#
#   scripts/spikes/macos-install.sh --assets DIR [--online] [--root DIR] [--repo DIR]
#       [--fixture FILE] [--libreoffice DMG] [--vendored] [--quarantine-test]
#
#   --assets DIR      holds assets.tsv (kind, name, file, sha256, url; made from
#                     scripts/spikes/spike_assets.py) and any files already fetched.
#   --online          download files missing from DIR (curl). Without it every file must
#                     already be in DIR: the offline variant. Either way every file is
#                     checked against its pinned SHA-256 and the install itself reads only
#                     local files, so online and offline share one code path.
#   --root DIR        default: ~/Library/Application Support/opncafyr/spike
#   --r-root DIR      where R itself goes; must not contain a space (renv runs
#                     `R CMD config`, i.e. make with R_HOME/etc/Makeconf, which breaks on
#                     one). Default: ~/.opncafyr-spike/r
#   --repo DIR        opncafyr checkout (runtime/python/tools); default: this script's repo
#   --fixture FILE    quartifyr v0.3.4 source archive (for examples/demo-report);
#                     default: DIR/quartifyr-v0.3.4-src.tar.gz
#   --libreoffice DMG also run the #8 LibreOffice checks with this DMG
#   --vendored        also try the vendored opnca-r source build (repair path, ADR 0004)
#   --quarantine-test also check what Gatekeeper does with quarantined copies
#
# Each step's output goes to <root>/logs/<step>.log and a line to <root>/logs/steps.tsv
# (step, seconds, exit code). The offline variant runs the whole script under
# sandbox-exec with outbound network denied except loopback.
set -uo pipefail

ASSETS=""; ONLINE=0; VENDORED=0; QTEST=0; LODMG=""; FIXTURE=""
ROOT="${HOME}/Library/Application Support/opncafyr/spike"
RROOT="${HOME}/.opncafyr-spike/r"
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
while [ $# -gt 0 ]; do
  case "$1" in
    --assets) ASSETS="$(cd "$2" && pwd)"; shift 2 ;;
    --online) ONLINE=1; shift ;;
    --root) ROOT="$2"; shift 2 ;;
    --r-root) RROOT="$2"; shift 2 ;;
    --repo) REPO="$(cd "$2" && pwd)"; shift 2 ;;
    --fixture) FIXTURE="$2"; shift 2 ;;
    --libreoffice) LODMG="$2"; shift 2 ;;
    --vendored) VENDORED=1; shift ;;
    --quarantine-test) QTEST=1; shift ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
[ -n "$ASSETS" ] && [ -f "$ASSETS/assets.tsv" ] || { echo "--assets DIR with assets.tsv is required" >&2; exit 2; }
FIXTURE="${FIXTURE:-$ASSETS/quartifyr-v0.3.4-src.tar.gz}"
if [ "$(id -u)" -eq 0 ]; then echo "do not run as root" >&2; exit 2; fi

ARCH="$(uname -m)"
case "$ARCH" in arm64) PLATFORM=macos-arm64 ;; x86_64) PLATFORM=macos-x86_64 ;; *) exit 2 ;; esac
LOG="$ROOT/logs"; DL="$ROOT/downloads"
case "$RROOT" in *" "*) echo "--r-root must not contain a space" >&2; exit 2 ;; esac
rm -rf "$ROOT" "$RROOT"; mkdir -p "$LOG" "$DL" "$ROOT/bin" "$ROOT/tmp" "$RROOT"
STEPS="$LOG/steps.tsv"; : > "$STEPS"
MARKER="$ROOT/tmp/.start"; touch "$MARKER"

# Only these tools from the base system; nothing from Homebrew, /usr/local or the CLT.
export PATH="/usr/bin:/bin:/usr/sbin:/sbin"
# R refuses a temporary directory whose path contains a space ("Fatal error: 'R_TempDir'
# contains space"), and ~/Library/Application Support has one. Use the per-user
# temporary directory macOS provides (/var/folders/...), which never does.
TMPDIR="$(getconf DARWIN_USER_TEMP_DIR)opncafyr-spike"; export TMPDIR
rm -rf "$TMPDIR"; mkdir -p "$TMPDIR"

step() {
  local name="$1"; shift
  local t0 t rc
  t0=$(date +%s)
  ( set -e; "$@" ) > "$LOG/$name.log" 2>&1
  rc=$?
  t=$(( $(date +%s) - t0 ))
  printf '%s\t%s\t%s\n' "$name" "$t" "$rc" >> "$STEPS"
  printf '[%s] %ss exit=%s\n' "$name" "$t" "$rc"
  if [ "$rc" -ne 0 ]; then tail -n 25 "$LOG/$name.log" | sed 's/^/    /'; fi
  return "$rc"
}
# Never call step as an if condition or in &&/||: bash then ignores set -e inside it.
must() {
  step "$@"
  local rc=$?
  if [ "$rc" -ne 0 ]; then echo "stopping: $1 failed"; summary; exit 1; fi
}

summary() {
  echo; echo "== steps =="; column -t -s $'\t' "$STEPS" 2>/dev/null || cat "$STEPS"
}

# ---- asset lookup -------------------------------------------------------------------
# assets.tsv: kind  name  file  sha256  url
asset() {  # asset <kind> [name] -> path of the verified local file
  awk -F'\t' -v k="$1" -v n="${2:-}" '$1==k && (n=="" || $2==n) {print $3; exit}' "$ASSETS/assets.tsv" |
    while read -r f; do
      if [ -f "$ASSETS/$f" ]; then echo "$ASSETS/$f"; else echo "$DL/$f"; fi
    done
}

s_preflight() {
  sw_vers; echo "arch: $ARCH"; id; echo "groups: $(id -Gn)"
  if id -Gn | tr ' ' '\n' | grep -qx admin; then echo "ERROR: user is an admin"; return 1; fi
  echo "gatekeeper: $(spctl --status 2>&1)"
  echo "SIP: $(csrutil status 2>&1)"
  echo "CLT: $(xcode-select -p 2>&1 || true)"
  if [ -e /Library/Frameworks/R.framework ]; then echo "ERROR: R.framework present"; return 1; fi
  echo "/Library/Frameworks: $(ls /Library/Frameworks | tr '\n' ' ')"
  echo "R on PATH: $(command -v R Rscript || echo none)"
  df -h "$HOME" | tail -1
}

s_fetch() {
  local n=0 dl=0 kind name file sha url path got
  while IFS=$'\t' read -r kind name file sha url; do
    [ -n "$url" ] || continue   # missing-wheel rows: reported by tools_env
    path="$ASSETS/$file"
    if [ ! -f "$path" ]; then
      path="$DL/$file"
      if [ "$ONLINE" -ne 1 ]; then echo "missing (offline): $file"; return 1; fi
      case "$url" in *mantra-opnca*) echo "private asset must be pre-fetched: $file"; return 1 ;; esac
      curl -fsSL --retry 3 -o "$path" "$url"; dl=$((dl + 1))
    fi
    got=$(shasum -a 256 "$path" | awk '{print $1}')
    if [ "$got" != "$sha" ]; then echo "SHA-256 mismatch: $file"; return 1; fi
    if xattr -p com.apple.quarantine "$path" >/dev/null 2>&1; then echo "quarantined: $file"; fi
    n=$((n + 1))
  done < "$ASSETS/assets.tsv"
  echo "verified $n files ($dl downloaded)"
}

# ---- Python -------------------------------------------------------------------------
UVDIR="$ROOT/uv"; UV="$UVDIR/uv"
export UV_CACHE_DIR="$ROOT/uv-cache" UV_PYTHON_INSTALL_DIR="$ROOT/python" UV_NO_CONFIG=1
export UV_PYTHON_INSTALL_MIRROR="file://$ROOT/python-mirror" UV_PYTHON_DOWNLOADS=manual
export UV_OFFLINE=1 UV_NO_INDEX=1 UV_FIND_LINKS="$ROOT/wheelhouse"

s_uv() {
  mkdir -p "$UVDIR"; tar xzf "$(asset uv)" -C "$UVDIR" --strip-components 1
  "$UV" --version
}

s_python() {
  local py build
  py="$(asset python)"; build="$(awk -F'\t' '$1=="python"{print $5}' "$ASSETS/assets.tsv" | cut -d/ -f8)"
  mkdir -p "$ROOT/python-mirror/$build"; ln -sf "$py" "$ROOT/python-mirror/$build/"
  "$UV" python install 3.13.18 --no-bin
  "$UV" python find 3.13.18
}

TOOLS="$ROOT/envs/tools"
s_tools_env() {
  # uv sync --locked can't install the URL-pinned deckifyr wheel offline (it always
  # fetches the URL), so install the exported lock from the wheelhouse instead.
  mkdir -p "$ROOT/wheelhouse" "$ROOT/tmp/toolsproj"
  awk -F'\t' '$1=="wheel"||$1=="package:deckifyr"{print $3}' "$ASSETS/assets.tsv" | sort -u |
    while read -r f; do ln -sf "$(asset_file "$f")" "$ROOT/wheelhouse/"; done
  cp "$REPO/runtime/python/tools/pyproject.toml" "$REPO/runtime/python/tools/uv.lock" "$ROOT/tmp/toolsproj/"
  ( cd "$ROOT/tmp/toolsproj" && "$UV" export --frozen --no-emit-project --format requirements-txt ) > "$ROOT/tmp/tools-req.txt"
  sed -i '' "s#https://github.com/jprybylski/deckifyr/releases/download/v0.3.5/#file://$ROOT/wheelhouse/#" "$ROOT/tmp/tools-req.txt"
  # A platform without a wheel for a package (pymupdf 2.x has none for macOS x86_64)
  # is reported and that package skipped; deckifyr previews then can't run.
  local missing
  missing=$(awk -F'\t' '$1=="missing-wheel"{print $2}' "$ASSETS/assets.tsv")
  for m in $missing; do
    echo "NO WHEEL for $m on $PLATFORM: skipped"
    awk -v m="$m" 'BEGIN{skip=0} { if ($0 ~ "^"m"==") {skip=1; next} if (skip && $0 ~ /^    /) next; skip=0; print }' \
      "$ROOT/tmp/tools-req.txt" > "$ROOT/tmp/r2" && mv "$ROOT/tmp/r2" "$ROOT/tmp/tools-req.txt"
  done
  "$UV" venv --python 3.13.18 "$TOOLS"
  # --no-deps: the export already lists every pinned package, and a skipped one must
  # not make its dependants (pymupdf-layout, pymupdf4llm) unresolvable.
  "$UV" pip install --python "$TOOLS/bin/python" --no-index --find-links "$ROOT/wheelhouse" \
    --require-hashes --no-deps -r "$ROOT/tmp/tools-req.txt"
  "$TOOLS/bin/python" -c "import deckifyr, docx, yaml, PIL, fastapi, uvicorn; print('tools env ok')"
  "$TOOLS/bin/python" -c "import pymupdf" 2>/dev/null && echo "pymupdf ok" || echo "pymupdf MISSING"
}
asset_file() { if [ -f "$ASSETS/$1" ]; then echo "$ASSETS/$1"; else echo "$DL/$1"; fi; }

# ---- R ------------------------------------------------------------------------------
RDEST="$RROOT/4.6.2"
s_r() {
  # rig needs sudo on macOS (it installs /Library/Frameworks/R.framework). Instead,
  # expand CRAN's .pkg and use its R.framework from the app root. No binary is changed:
  # R's own bin/R sources etc/ldpaths, which sets DYLD_FALLBACK_LIBRARY_PATH to
  # R_HOME/lib, so every absolute /Library/Frameworks/R.framework/... reference
  # (libR, libRblas, libgfortran, libomp, also in package .so files) falls back to the
  # relocated copy. exec/R is signed with allow-dyld-environment-variables and
  # disable-library-validation, so dyld honours that and loads package binaries.
  local tmp="$ROOT/tmp/rpkg" rh
  rm -rf "$tmp"; pkgutil --expand-full "$(asset r)" "$tmp"
  mkdir -p "$RDEST"; ditto "$tmp/R-fw.pkg/Payload/R.framework" "$RDEST/R.framework"
  rm -rf "$tmp"
  rh="$(cd "$RDEST/R.framework/Resources" && pwd -P)"
  # Text files that name the framework path: bin/R (R_HOME_DIR, share, include, doc),
  # etc/Renviron (qpdf), etc/Makeconf (-F for source builds).
  grep -rlI '/Library/Frameworks/R.framework' "$rh/bin" "$rh/etc" | while read -r f; do
    echo "rewriting $f"
    sed -i '' "s#/Library/Frameworks/R.framework#$RDEST/R.framework#g" "$f"
  done
  # A system-wide CRAN R of the same minor (/Library/Frameworks/R.framework/Versions/4.6)
  # would win over the fallback path, since dyld tries the absolute path first: the
  # relocated R then runs on the user's libR. DYLD_LIBRARY_PATH is searched before
  # absolute paths, so set it too. bin/R sources ldpaths after /bin/sh has started, so
  # SIP's stripping of DYLD_* variables doesn't affect it.
  printf '\n## opncafyr: relocated R; load its own libraries even when a system R exists.\nDYLD_LIBRARY_PATH="${R_HOME}/lib${DYLD_LIBRARY_PATH:+:${DYLD_LIBRARY_PATH}}"\nexport DYLD_LIBRARY_PATH\n' >> "$rh/etc/ldpaths"
  # R_HOME/bin/Rscript has the framework path compiled in but honours RHOME. Export it
  # from bin/R, so Rscript started by R itself (package configure scripts, R CMD) works.
  sed -i '' 's#^export R_HOME$#export R_HOME\
RHOME="${R_HOME}"; export RHOME#' "$rh/bin/R"
  grep -n '^RHOME=' "$rh/bin/R"
  # Rscript has the framework path compiled in but honours RHOME.
  printf '#!/bin/sh\nRHOME=%q\nexport RHOME\nexec %q "$@"\n' "$rh" "$rh/bin/Rscript" > "$ROOT/bin/Rscript"
  printf '#!/bin/sh\nexec %q "$@"\n' "$rh/bin/R" > "$ROOT/bin/R"
  chmod +x "$ROOT/bin/Rscript" "$ROOT/bin/R"
  "$ROOT/bin/Rscript" -e 'cat(R.version.string, R.home(), .Platform$pkgType, sep = "\n"); print(.libPaths()); print(capabilities()[c("png","cairo","aqua")])
    m <- system2("/usr/sbin/lsof", c("-p", Sys.getpid()), stdout = TRUE)
    cat("loaded:", unique(regmatches(m, regexpr("/[^ ]*lib(R|Rblas|gfortran)[.][^ ]*dylib", m))), sep = "\n")
    stopifnot(R.version$minor == "6.2")'
  codesign --verify --verbose "$rh/bin/exec/R"
  test ! -e /Library/Frameworks/R.framework
}

RLIB="$ROOT/r-library"; RREPO="$ROOT/r-repo"; CELLAR="$ROOT/r-cellar"
s_r_repo() {
  local contrib
  contrib=$("$ROOT/bin/Rscript" -e 'cat(sub("^file://", "", contrib.url("file://REPO", type = .Platform$pkgType)))' | sed "s#^REPO#$RREPO#")
  mkdir -p "$contrib" "$RREPO/src/contrib" "$CELLAR" "$RLIB"
  awk -F'\t' '$1=="r-binary"{print $3}' "$ASSETS/assets.tsv" | while read -r f; do ln -sf "$(asset_file "$f")" "$contrib/"; done
  "$ROOT/bin/Rscript" -e 'tools::write_PACKAGES(commandArgs(TRUE)[1], type = "mac.binary"); tools::write_PACKAGES(commandArgs(TRUE)[2], type = "source")' "$contrib" "$RREPO/src/contrib"
  # Cellar: opnca-r binary under the name renv expects, and the pure-R tarballs.
  cp "$(asset_file "opnca_0.3.3_R4.6_${PLATFORM}.tgz")" "$CELLAR/opnca_0.3.3.tgz"
  for p in quartifyr reportifyr pyro; do cp "$(asset "package:$p")" "$CELLAR/"; done
  ls "$CELLAR"; echo "binaries: $(find "$contrib" -name '*.tgz' | wc -l | tr -d ' ')"
}

s_r_restore() {
  # Recorded, not required: see s_r_install.
  mkdir -p "$ROOT/tmp/rproj" "$ROOT/tmp/renv-lib"; cp "$REPO/runtime/r/renv.lock" "$ROOT/tmp/rproj/"
  cd "$ROOT/tmp/rproj"
  echo "CLT: $(xcode-select -p 2>&1)"
  RENV_PATHS_ROOT="$ROOT/renv" RENV_PATHS_CELLAR="$CELLAR" R_LIBS_USER="$ROOT/tmp/renv-lib" R_LIBS_SITE="" \
    RENV_CONFIG_AUTOLOADER_ENABLED=FALSE "$ROOT/bin/Rscript" --vanilla -e '
      lib <- Sys.getenv("R_LIBS_USER"); repo <- paste0("file://", commandArgs(TRUE)[1])
      .libPaths(lib)
      install.packages("renv", lib = lib, repos = repo, type = "binary", quiet = TRUE)
      library(renv, lib.loc = lib, warn.conflicts = FALSE)
      options(repos = c(P3M = repo), renv.config.repos.override = repo)
      renv::restore(lockfile = "renv.lock", library = lib, prompt = FALSE)
      lock <- jsonlite::read_json("renv.lock")$Packages
      bad <- Filter(function(p) !identical(tryCatch(packageDescription(p, lib.loc = lib, fields = "Version"),
                                                    warning = function(w) NA), lock[[p]]$Version), names(lock))
      cat("locked:", length(lock), "missing or wrong version:", length(bad), "\n")
      if (length(bad)) { print(bad); quit(status = 1) }
      for (p in c("opnca", "quartifyr", "reportifyr", "plumber2", "flextable", "data.table"))
        suppressPackageStartupMessages(library(p, character.only = TRUE, lib.loc = lib))
      cat("loaded opnca, quartifyr, reportifyr, plumber2, flextable, data.table\n")
      # Which libR/libomp did dyld actually use?
      maps <- tryCatch(system2("/usr/sbin/lsof", c("-p", Sys.getpid()), stdout = TRUE, stderr = FALSE),
                       error = function(e) character(), warning = function(w) character())
      cat("mapped:", unique(grep("libR\\.dylib|libomp\\.dylib", sub(".* (/.*)$", "\\1", maps), value = TRUE)), sep = "\n")
    ' "$RREPO"
}

s_r_install() {
  # renv::restore() on macOS always runs `R CMD config CC` (renv_scope_install_macos),
  # which calls make; without the Command Line Tools /usr/bin/make is only an installer
  # stub, so renv fails before installing anything, even binaries. Base R needs no
  # toolchain for binaries or for pure-R source packages: install exactly the locked
  # files from the local repository and the cellar, then check every version against
  # renv.lock.
  rm -rf "$RLIB"; mkdir -p "$RLIB"
  R_LIBS_USER="$RLIB" R_LIBS_SITE="" "$ROOT/bin/Rscript" --vanilla -e '
    a <- commandArgs(TRUE); lib <- a[1]; repo <- paste0("file://", a[2]); cellar <- a[3]; lockfile <- a[4]
    .libPaths(lib)
    install.packages("jsonlite", lib = lib, repos = repo, type = "binary", quiet = TRUE)
    lock <- jsonlite::read_json(lockfile)$Packages
    from_repo <- names(Filter(function(e) identical(e$Source, "Repository"), lock))
    install.packages(setdiff(from_repo, "jsonlite"), lib = lib, repos = repo, type = "binary",
                     dependencies = FALSE, quiet = TRUE)
    install.packages(file.path(cellar, "opnca_0.3.3.tgz"), lib = lib, repos = NULL, type = "binary")
    for (f in c("pyro_0.1.1.tar.gz", "reportifyr_0.4.0.tar.gz", "quartifyr_0.3.4.tar.gz"))
      install.packages(file.path(cellar, f), lib = lib, repos = NULL, type = "source")
    bad <- Filter(function(p) !identical(tryCatch(packageDescription(p, lib.loc = lib, fields = "Version"),
                                                  warning = function(w) NA), lock[[p]]$Version), names(lock))
    cat("locked:", length(lock), "installed:", length(list.files(lib)), "missing or wrong version:", length(bad), "\n")
    if (length(bad)) { print(bad); quit(status = 1) }
    for (p in c("opnca", "quartifyr", "reportifyr", "plumber2", "flextable", "data.table"))
      suppressPackageStartupMessages(library(p, character.only = TRUE, lib.loc = lib))
    cat("loaded opnca, quartifyr, reportifyr, plumber2, flextable, data.table\n")
    m <- system2("/usr/sbin/lsof", c("-p", Sys.getpid()), stdout = TRUE)
    cat("mapped:", unique(regmatches(m, regexpr("/[^ ]*lib(R|omp)[.]dylib", m))), sep = "\n")
  ' "$RLIB" "$RREPO" "$CELLAR" "$REPO/runtime/r/renv.lock"
}

s_opnca_calc() {
  R_LIBS_USER="$RLIB" "$ROOT/bin/Rscript" --vanilla -e '
    .libPaths(Sys.getenv("R_LIBS_USER")); library(opnca)
    d <- data.frame(USUBJID = c(rep("P1", 5), rep("P2", 5)), ACTUAL_TIME = c(0, 1, 2, 4, 8, 0, 1, 2, 4, 8),
                    CONC = c(0, 10, 5, 2.5, 0.625, 0, 20, 10, 5, 1.25), DOSE = rep(100, 10),
                    ROUTE = rep("extravascular", 10))
    x <- as.data.frame(opnca(d, metrics = c("cmax", "tmax", "auclast", "lambda_z", "half_life")))
    # Reference: opnca-r 0.2.7 to 0.3.2 on the dev machine (and the tests in opnca-r test-basic-nca.R).
    ref <- c(P1.cmax = 10, P1.tmax = 1, P1.auclast = 24.837056812223246, P1.lambda_z = 0.346573590279973,
             P1.half_life = 2, P2.cmax = 20, P2.tmax = 1, P2.auclast = 49.674113624446498,
             P2.lambda_z = 0.346573590279973, P2.half_life = 2)
    got <- setNames(x$value, paste(x$profile_id, x$metric, sep = "."))[names(ref)]
    print(cbind(got, ref), digits = 15)
    stopifnot(max(abs(got - ref)) < 1e-12); cat("opnca results match the reference\n")'
}

s_opnca_serve() {
  local out="$ROOT/tmp/opnca-run" a b port=47611 i
  mkdir -p "$out"
  a="spike-api-$(openssl rand -hex 16)"; b="spike-stop-$(openssl rand -hex 16)"
  R_LIBS_USER="$RLIB" OPNCA_API_TOKEN="$a" OPNCA_SHUTDOWN_TOKEN="$b" "$ROOT/bin/Rscript" --vanilla \
    "$RLIB/opnca/scripts/serve.R" --data-path="$RLIB/opnca/extdata/theoph_sample.csv" \
    --output-dir="$out" --port=$port --base-path=/apps/opnca/ --lock-dataset --no-persist-mapping \
    --storage-namespace=spike --no-overwrite --require-blq-rules \
    --api-token-env=OPNCA_API_TOKEN --shutdown-token-env=OPNCA_SHUTDOWN_TOKEN \
    > "$ROOT/tmp/serve.out" 2> "$ROOT/tmp/serve.err" &
  local pid=$!
  for i in $(seq 1 120); do grep -q OPNCA_READY "$ROOT/tmp/serve.out" && break; sleep 1; done
  echo "ready after ${i}s: $(cat "$ROOT/tmp/serve.out")"
  curl -fsS -H "X-Opnca-Api-Token: $a" "http://127.0.0.1:$port/apps/opnca/api/v1/health"; echo
  # Since opnca-r 0.2.8 the whole 202 body arrives before the server stops (opnca-r#47),
  # so a truncated response (curl exit 18) is a failure again.
  curl -fsS -o /dev/null -w 'shutdown %{http_code}\n' -X POST -H "X-Opnca-Api-Token: $a" \
    -H "X-Opnca-Shutdown-Token: $b" "http://127.0.0.1:$port/apps/opnca/api/v1/shutdown"
  for i in $(seq 1 30); do kill -0 "$pid" 2>/dev/null || break; sleep 1; done
  if kill -0 "$pid" 2>/dev/null; then echo "server still running after shutdown"; kill "$pid"; return 1; fi
  echo "server stopped"
}

s_deckifyr() {
  cd "$ROOT/tmp" && rm -rf deck
  "$TOOLS/bin/python" -m deckifyr --json init deck
  "$TOOLS/bin/python" -m deckifyr --json build --trusted-binaries-only \
    --rscript-bin "$ROOT/bin/Rscript" deck/presentation.yaml > deck-build.json
  cat deck-build.json; grep -q '"status": "ok"' deck-build.json
  ls -l deck/build/*.pptx
}

QUARTO="$ROOT/quarto/1.11.9/bin/quarto"
s_quarto() {
  mkdir -p "$ROOT/quarto/1.11.9"; tar xzf "$(asset quarto)" -C "$ROOT/quarto/1.11.9"
  "$QUARTO" --version
}

REPORT="$ROOT/tmp/report"
report_env() {
  env PATH="$UVDIR:$PATH" R_LIBS_USER="$RLIB" R_LIBS="$RLIB" QUARTIFYR_PYTHON_ENV="$TOOLS" \
    UV_NO_SYNC=1 "$@"
}
s_report() {
  # The app creates report projects itself; here quartifyr's demo report stands in,
  # without its committed pyproject.toml/uv.lock/renv (an app-made project has none).
  rm -rf "$REPORT"; mkdir -p "$REPORT"
  tar xzf "$FIXTURE" -C "$REPORT" --strip-components 3 "quartifyr-0.3.4/examples/demo-report"
  rm -rf "$REPORT/.Rprofile" "$REPORT/renv" "$REPORT/renv.lock" "$REPORT/pyproject.toml" "$REPORT/uv.lock"
  cd "$REPORT"
  # reportifyr's project setup goes through pyro: `uv lock` + `uv sync` of its pinned
  # group, resolved from the wheelhouse (same pins as the tools env), with our uv first
  # on PATH so pyro never curl-installs its own.
  report_env "$ROOT/bin/Rscript" --vanilla -e '.libPaths(Sys.getenv("R_LIBS_USER")); reportifyr::initialize_report_project(getwd())'
  report_env "$ROOT/bin/Rscript" --vanilla "$RLIB/quartifyr/scripts/render.R" --project "$REPORT" \
    --shell report.qmd --json --strict --offline --quarto-bin "$QUARTO" > "$ROOT/tmp/render.json"
  cat "$ROOT/tmp/render.json"
  ls -l "$REPORT/report/draft/"*.docx
}

s_postflight() {
  if [ -e /Library/Frameworks/R.framework ]; then echo "R.framework appeared"; return 1; fi
  du -sh "$RROOT"
  echo "no /Library/Frameworks/R.framework"
  echo "files changed under HOME outside the app root (top level):"
  find "$HOME" -newer "$MARKER" -type f 2>/dev/null | grep -v "^$ROOT" | sed "s#^$HOME/##" |
    grep -v '^\.opncafyr-spike/' | awk -F/ '{print $1"/"$2}' | sort | uniq -c | sort -rn | head -20
  du -sh "$ROOT"/* | sort -h | tail -12
}

# ---- optional: quarantine, vendored build, LibreOffice ----------------------------
s_quarantine() {
  # A bundle downloaded with a browser is quarantined, and tar/ditto/Archive Utility
  # copy the attribute to everything they extract. Quarantine each installed runtime
  # in place, run it, and record what Gatekeeper does; then remove the attribute again.
  local stamp q
  stamp="0081;$(printf %x "$(date +%s)");Safari;"
  quarantine() { find "$1" -exec xattr -sw com.apple.quarantine "$stamp" {} + 2>/dev/null || true; }
  unquarantine() { find "$1" -exec xattr -sd com.apple.quarantine {} + 2>/dev/null || true; }
  try() {  # try <label> <command...>
    local rc=0
    with_timeout "${@:2}" > "$TMPDIR/q.out" 2>&1 || rc=$?
    echo "== $1: rc=$rc $(head -c 200 "$TMPDIR/q.out" | tr '\n' ' ')"
  }
  for q in "$UVDIR" "$ROOT/python" "$RDEST" "$ROOT/quarto"; do quarantine "$q"; done
  echo "uv: $(spctl -a -vv -t exec "$UV" 2>&1 | tr '\n' ' ')"
  echo "R exec: $(spctl -a -vv -t exec "$RDEST/R.framework/Resources/bin/exec/R" 2>&1 | tr '\n' ' ')"
  echo "python: $(codesign -dv "$(ls "$ROOT"/python/*/bin/python3.13 | head -1)" 2>&1 | grep -E 'Authority|Signature' | tr '\n' ' ')"
  echo "quarto deno: $(codesign -dv "$ROOT/quarto/1.11.9/bin/tools/"*/deno 2>&1 | grep -E 'Authority|Signature' | head -2 | tr '\n' ' ')"
  try uv "$UV" --version
  try python "$TOOLS/bin/python" -c 'import sys; print(sys.version)'
  try Rscript "$ROOT/bin/Rscript" -e 'cat(R.version.string)'
  try quarto "$QUARTO" --version
  try deckifyr "$TOOLS/bin/python" -m deckifyr --help
  for q in "$UVDIR" "$ROOT/python" "$RDEST" "$ROOT/quarto"; do unquarantine "$q"; done
  try "Rscript after removing quarantine" "$ROOT/bin/Rscript" -e 'cat(R.version.string)'
}

s_vendored() {
  # Repair path only (ADR 0004): build opnca_0.3.3-vendored.tar.gz. Needs a C toolchain.
  echo "CLT: $(xcode-select -p 2>&1)"; echo "clang: $(/usr/bin/clang --version 2>&1 | head -1)"
  local rs; rs="$(asset rust-standalone)"
  [ -n "$rs" ] || { echo "no rust standalone asset in assets.tsv"; return 1; }
  # Rust's install.sh can't take a prefix with a space either.
  local rust="$RROOT/../rust"
  mkdir -p "$TMPDIR/rust-src" "$rust"
  tar xJf "$rs" -C "$TMPDIR/rust-src" --strip-components 1
  "$TMPDIR/rust-src/install.sh" --prefix="$rust" --disable-ldconfig > /dev/null
  "$rust/bin/cargo" --version; "$rust/bin/rustc" --version
  mkdir -p "$ROOT/tmp/vlib"
  PATH="$rust/bin:$PATH" NOT_CRAN="" R_LIBS="$RLIB" "$ROOT/bin/R" CMD INSTALL --library="$ROOT/tmp/vlib" \
    "$(asset_file opnca_0.3.3-vendored.tar.gz)"
}

s_libreoffice() {
  # #8: per-user LibreOffice from the DMG, no admin; preview and recalc 20x each way.
  # The profile URL must not contain a raw space (soffice aborts with an uncaught
  # css::uno::RuntimeException); keep profiles under the space-free TMPDIR.
  local mnt="$ROOT/tmp/lo-mnt" app="$ROOT/libreoffice/LibreOffice.app" soffice t0 i ok fail
  hdiutil attach -nobrowse -readonly -mountpoint "$mnt" "$LODMG"
  mkdir -p "$ROOT/libreoffice"; ditto "$mnt/LibreOffice.app" "$app"; hdiutil detach "$mnt"
  echo "quarantine on app: $(xattr -p com.apple.quarantine "$app" 2>&1 | head -1)"
  du -sh "$app"; soffice="$app/Contents/MacOS/soffice"
  echo "spctl: $(spctl -a -vv "$app" 2>&1 | tr '\n' ' ')"
  t0=$(date +%s)
  "$soffice" --headless -env:UserInstallation="file://${TMPDIR}/lo-profile-first" --version
  echo "first run (version): $(( $(date +%s) - t0 ))s"
  cp "$ROOT/tmp/deck/build/"*.pptx "$ROOT/tmp/first.pptx"
  # Headless soffice still needs the user's GUI login session on macOS: started from
  # an SSH-only session it hangs here. The launcher runs in the desktop session.
  t0=$(date +%s)
  with_timeout "$soffice" --headless -env:UserInstallation="file://${TMPDIR}/lo-profile-first" \
    --convert-to pdf --outdir "$ROOT/tmp/lo-first" "$ROOT/tmp/first.pptx"
  echo "first convert: $(( $(date +%s) - t0 ))s"
  lo_runs preview "$soffice"; lo_runs recalc "$soffice"
}
TMO=300
with_timeout() { perl -e '$t=shift; $p=fork; if(!$p){setpgrp(0,0); exec @ARGV} $SIG{ALRM}=sub{kill -9, $p; exit 124}; alarm $t; waitpid($p,0); exit($?>>8)' "$TMO" "$@"; }
lo_one() {  # lo_one <kind> <soffice> <n>
  local kind="$1" soffice="$2" n="$3" d="$ROOT/tmp/lo-$1-$3"
  rm -rf "$d"; mkdir -p "$d"
  if [ "$kind" = preview ]; then
    cp -R "$ROOT/tmp/deck/." "$d/"
    with_timeout "$TOOLS/bin/python" -m deckifyr --json preview --trusted-binaries-only \
      --soffice-bin "$soffice" --rscript-bin "$ROOT/bin/Rscript" "$d/presentation.yaml" > "$d/out.json" 2> "$d/err.txt"
  else
    cp -R "$REPORT/." "$d/"
    ( cd "$d" && report_env env PATH="$(dirname "$soffice"):$UVDIR:$PATH" "$ROOT/bin/Rscript" --vanilla \
        "$RLIB/quartifyr/scripts/render.R" --project "$d" --shell report.qmd --json --strict --offline \
        --recalculate-fields --quarto-bin "$QUARTO" > "$d/out.json" 2> "$d/err.txt" ) &
    local pid=$!; ( sleep $TMO; kill -9 $pid 2>/dev/null ) & local w=$!
    local rc=0; wait $pid || rc=$?; kill $w 2>/dev/null || true; return $rc
  fi
}
lo_runs() {
  local kind="$1" soffice="$2" i rc ok=0 fail=0 t0
  t0=$(date +%s)
  for i in $(seq 1 20); do
    rc=0; lo_one "$kind" "$soffice" "s$i" || rc=$?
    if [ $rc -eq 0 ]; then ok=$((ok+1)); else fail=$((fail+1)); echo "$kind s$i rc=$rc"; fi
  done
  echo "$kind sequential: $ok ok, $fail failed, $(( $(date +%s) - t0 ))s"
  ok=0; fail=0; t0=$(date +%s)
  for b in 1 2 3 4 5; do
    local pids=() names=()
    for j in 1 2 3 4; do lo_one "$kind" "$soffice" "c$b-$j" & pids+=($!); names+=("c$b-$j"); done
    for k in 0 1 2 3; do
      rc=0; wait "${pids[$k]}" || rc=$?
      if [ $rc -eq 0 ]; then ok=$((ok+1)); else fail=$((fail+1)); echo "$kind ${names[$k]} rc=$rc"; fi
    done
  done
  echo "$kind concurrent (4-way): $ok ok, $fail failed, $(( $(date +%s) - t0 ))s"
  if pgrep -fl soffice; then echo "LEFTOVER soffice processes"; else echo "no soffice left"; fi
}

# ---- run ----------------------------------------------------------------------------
echo "opncafyr macOS install spike: $PLATFORM, root: $ROOT, online=$ONLINE"
must preflight s_preflight
must fetch s_fetch
must uv s_uv
must python s_python
must tools_env s_tools_env
must r s_r
must r_repo s_r_repo
step r_renv_restore s_r_restore
must r_install s_r_install
step opnca_calc s_opnca_calc
step opnca_serve s_opnca_serve
step deckifyr s_deckifyr
must quarto s_quarto
step report s_report
if [ "$QTEST" -eq 1 ]; then step quarantine s_quarantine; fi
if [ "$VENDORED" -eq 1 ]; then step vendored s_vendored; fi
if [ -n "$LODMG" ]; then step libreoffice s_libreoffice; fi
step postflight s_postflight
summary
# Exit status: the required chain only. r_renv_restore, quarantine and vendored record
# findings (they are expected to fail on a Mac without the Command Line Tools).
awk -F'\t' '$1 !~ /^(r_renv_restore|quarantine|vendored)$/ && $3 != 0 {bad=1} END {exit bad}' "$STEPS"
