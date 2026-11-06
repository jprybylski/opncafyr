#!/usr/bin/env bash
# Offline phase of the offline CI job (#44): install from local assets, then run the flow.
#
#   scripts/offline/offline_phase.sh <assets dir from fetch.py> <work dir>
#
# Run it with the network cut (the CI job uses a network namespace and strace; see
# docs/offline.md). It never names a URL: every input is a file under <assets> or this
# repository. Environment:
#   RSCRIPT   Rscript of the pinned R (default /opt/R/4.6.2/bin/Rscript)
#   JOBS      parallel build jobs (default: nproc)
#
# Steps (each prints "== step" and its duration; any failure stops the run):
#   1. runtimes: uv, Python, Quarto and Rust from their archives
#   2. python: the launcher and tools environments from the wheelhouse, hash-checked
#      against the requirements exported from their uv.lock files
#   3. r: renv::restore() from the local repository and the cellar
#   4. import: scan, preview and import the two-analyte fixture through the app API
#   5. opnca: one opnca-r calculation on one analyte (skipped without opnca-r)
#   6. report: a quartifyr project with the tools environment, rendered --offline --strict
#   7. deck: deckifyr init + build with --trusted-binaries-only
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "usage: $0 <assets> <work>" >&2
  exit 2
fi
ASSETS="$(cd "$1" && pwd)"
mkdir -p "$2"
WORK="$(cd "$2" && pwd)"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
RSCRIPT="${RSCRIPT:-/opt/R/4.6.2/bin/Rscript}"
JOBS="${JOBS:-$(nproc)}"
RESULTS="$WORK/results"
mkdir -p "$RESULTS"

manifest() { python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))[sys.argv[2]][sys.argv[3]])' "$ASSETS/fetch-manifest.json" "$@"; }

# Nothing may reach for the network. These are the settings the launcher gives every tool
# process in offline mode (docs/offline.md).
export UV_OFFLINE=1 UV_NO_INDEX=1 UV_FIND_LINKS="$ASSETS/wheelhouse" UV_PYTHON_DOWNLOADS=never
export UV_CACHE_DIR="$WORK/uv-cache" UV_PYTHON_INSTALL_DIR="$WORK/uv-python" UV_NO_PROGRESS=1
export R_PROFILE_USER=/dev/null R_ENVIRON_USER=/dev/null R_LIBS_USER="$WORK/rlib" R_LIBS="$WORK/rlib"
export RENV_CONFIG_UPDATES_CHECK=FALSE
export MAKEFLAGS="-j$JOBS"
# Unpack as the current user even when run as (namespace) root, e.g. under `unshare -r`.
export TAR_OPTIONS=--no-same-owner
unset NOT_CRAN

step() {
  STEP="$1"
  STEP_START=$SECONDS
  echo "== $STEP"
}
done_step() {
  echo "   $STEP: $((SECONDS - STEP_START)) s"
  echo "{\"step\": \"$STEP\", \"seconds\": $((SECONDS - STEP_START))}" >> "$RESULTS/timings.jsonl"
}

step runtimes
mkdir -p "$WORK/rt"
tar --no-same-owner -xzf "$ASSETS/runtime/$(manifest runtimes uv)" -C "$WORK/rt"
UV_DIR="$(dirname "$(find "$WORK/rt" -maxdepth 2 -name uv -type f | head -1)")"
tar --no-same-owner -xzf "$ASSETS/runtime/$(manifest runtimes python)" -C "$WORK/rt"   # -> rt/python
tar --no-same-owner -xzf "$ASSETS/runtime/$(manifest runtimes quarto)" -C "$WORK/rt"
QUARTO="$(find "$WORK/rt" -maxdepth 3 -path '*/bin/quarto' -type f | head -1)"
mkdir -p "$WORK/rt/rust-src"
tar --no-same-owner -xJf "$ASSETS/runtime/$(manifest runtimes rust)" -C "$WORK/rt/rust-src" --strip-components=1
"$WORK/rt/rust-src/install.sh" --prefix="$WORK/rt/rust" --disable-ldconfig \
  --components=rustc,cargo,rust-std-x86_64-unknown-linux-gnu > "$WORK/rust-install.log"
rm -rf "$WORK/rt/rust-src"
PYTHON="$WORK/rt/python/bin/python3"
# The app-owned uv comes first, so pyro and reportifyr find it instead of installing one.
export PATH="$UV_DIR:$(dirname "$QUARTO"):$WORK/rt/rust/bin:$(dirname "$RSCRIPT"):/usr/bin:/bin"
uv --version
"$PYTHON" --version
quarto --version
cargo --version
done_step

step python
LAUNCHER_ENV="$WORK/envs/launcher/.venv"
TOOLS_ENV="$WORK/envs/tools/.venv"
# `uv sync --locked` can't install offline from a wheelhouse: it fetches each locked file by
# its recorded URL and only falls back to uv's cache, never to UV_FIND_LINKS (traced; see
# docs/offline.md I3). Install the requirements exported from the same locks instead, with
# every hash enforced.
uv venv --quiet --python "$PYTHON" "$LAUNCHER_ENV"
uv pip install --python "$LAUNCHER_ENV/bin/python" --require-hashes \
  --requirement "$ASSETS/requirements/launcher.offline.txt"
uv pip install --python "$LAUNCHER_ENV/bin/python" --no-deps "$ASSETS"/wheelhouse/opncafyr-*.whl
uv venv --quiet --python "$PYTHON" "$TOOLS_ENV"
uv pip install --python "$TOOLS_ENV/bin/python" --require-hashes \
  --requirement "$ASSETS/requirements/tools.offline.txt"
"$LAUNCHER_ENV/bin/python" -c 'import opncafyr, fastapi; print("launcher env ok")'
"$TOOLS_ENV/bin/python" -c 'import deckifyr, docx, yaml, PIL, pymupdf; print("tools env ok")'
done_step

step r
"$RSCRIPT" --vanilla "$REPO/scripts/offline/install_r.R" \
  "$REPO/runtime/r/renv.lock" "$ASSETS/rrepo" "$ASSETS/cellar" "$WORK/rlib"
done_step

step import
"$LAUNCHER_ENV/bin/python" "$REPO/scripts/offline/import_check.py" \
  "$REPO/tests/fixtures/two_analyte.csv" "$WORK/import" | tee "$RESULTS/import.json"
done_step

step opnca
if "$RSCRIPT" --vanilla -e 'quit(status = !requireNamespace("opnca", quietly = TRUE))'; then
  "$RSCRIPT" --vanilla "$REPO/scripts/offline/opnca_check.R" "$RESULTS/opnca.json"
else
  echo "   opnca-r is not installed (no release token): calculation skipped"
  echo '{"skipped": "opnca-r not available"}' > "$RESULTS/opnca.json"
fi
done_step

step report
PROJECT="$WORK/report"
rm -rf "$PROJECT"
cp -R "$REPO/scripts/offline/report" "$PROJECT"
# reportifyr::initialize_report_project() would run pyro (uv lock/sync, maybe a curl of
# uv). Lay the project out with reportifyr's own helpers instead, and hand reportifyr and
# quartifyr the tools environment (docs/offline.md, "Reports").
(cd "$PROJECT" && "$RSCRIPT" --vanilla -e '
  rd <- reportifyr:::create_report_directories(".", NULL)
  od <- reportifyr:::create_outputs_directories(".", NULL)
  reportifyr:::copy_footnotes(rd)
  reportifyr:::copy_config(rd, NULL, NULL)
  reportifyr:::create_init_file(".", rd, od)
  quartifyr::install_quarto_extensions(".")
  quartifyr::install_quartifyr_extension(".")')
# reportifyr reads the author from the global git config. Give it one in a throwaway home
# instead of touching the user's.
mkdir -p "$WORK/git-home"
printf '[user]\n\tname = opncafyr CI\n\temail = ci@opncafyr.invalid\n' > "$WORK/git-home/.gitconfig"
(cd "$PROJECT" && HOME="$WORK/git-home" "$RSCRIPT" --vanilla "$REPO/scripts/offline/make_report_outputs.R")
RENDER="$("$RSCRIPT" --vanilla -e 'cat(system.file("scripts", "render.R", package = "quartifyr"))')"
printf 'options(venv_dir = "%s")\n' "$(dirname "$TOOLS_ENV")" > "$WORK/report.Rprofile"
# UV_NO_PROJECT: reportifyr's `uv run` must not adopt a pyproject.toml found in a parent of
# the report project (here the repository's own; for users, any project their study folder
# sits in). It would then look for that project's environment instead (docs/offline.md R2).
(cd "$PROJECT" && R_PROFILE_USER="$WORK/report.Rprofile" QUARTIFYR_PYTHON_ENV="$TOOLS_ENV" \
  UV_NO_PROJECT=1 UV_NO_CONFIG=1 \
  "$RSCRIPT" "$RENDER" --project . --shell report.qmd --status draft --json --strict --offline \
  --quarto-bin "$QUARTO" > "$RESULTS/report.json")
python3 - "$RESULTS/report.json" <<'PY'
import json, sys
doc = json.load(open(sys.argv[1]))
assert doc["status"] == "ok", doc.get("errors")
print("report ok:", doc["draft"])
PY
done_step

step deck
DECK="$WORK/deck"
rm -rf "$DECK"
"$TOOLS_ENV/bin/python" -m deckifyr --json init "$DECK" > "$RESULTS/deck-init.json"
"$TOOLS_ENV/bin/python" -m deckifyr --json build "$DECK/presentation.yaml" \
  --trusted-binaries-only --quarto-bin "$QUARTO" --rscript-bin "$RSCRIPT" > "$RESULTS/deck.json"
python3 - "$RESULTS/deck.json" <<'PY'
import json, sys
doc = json.load(open(sys.argv[1]))
assert doc["status"] == "ok", doc
pptx = [a for a in doc["artifacts"] if a["kind"] == "pptx"]
assert pptx and open(pptx[0]["path"], "rb").read(2) == b"PK", "no PPTX"
print("deck ok:", pptx[0]["path"])
PY
done_step

echo "offline phase complete"
