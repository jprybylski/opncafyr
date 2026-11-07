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
#   4. flow: the Gate 2 flow through the launcher (scripts/offline/flow_check.py): import
#      the two-analyte fixture, clean, map, one run per analyte saved in opnca-r, replayed
#      and completed, then publish, render the DOCX report and build the PPTX deck. Every
#      tool process is started by the launcher's adapters, which apply the offline
#      settings themselves: the driver runs with a clean environment, so none of the
#      variables exported below reach it. Without opnca-r (no release token) the runs
#      stop at the snapshot and stand-in runs are published.
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

step flow
# The runtime record the installer would write, naming this job's pinned executables.
mkdir -p "$WORK/runtime" "$WORK/flow-tmp"
"$PYTHON" - "$WORK/runtime/runtime.json" <<PY
import json, sys
json.dump({
    "schema_version": 1,
    "executables": {
        "rscript": "$RSCRIPT",
        "quarto": "$QUARTO",
        "uv": "$UV_DIR/uv",
        "deckifyr_python": "$TOOLS_ENV/bin/python",
    },
    "r_library": "$WORK/rlib",
}, open(sys.argv[1], "w"), indent=2)
PY
env -i HOME="$HOME" PATH=/usr/bin:/bin TMPDIR="$WORK/flow-tmp" LANG=C.UTF-8 \
  OPNCAFYR_RUNTIME_FILE="$WORK/runtime/runtime.json" OPNCAFYR_APP_ROOT="$WORK/app" \
  "$LAUNCHER_ENV/bin/python" "$REPO/scripts/offline/flow_check.py" "$WORK/flow" \
  --results "$RESULTS"
done_step

echo "offline phase complete"
