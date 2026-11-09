#!/usr/bin/env bash
# Install the pinned Linux runtime from a fetched assets folder, with no network (#44, #42).
#
#   scripts/offline/install_runtime.sh <assets dir from fetch.py> <prefix>
#
# The one Linux install path: the offline CI job (offline_phase.sh) and the container
# image (container/Dockerfile) both run it. It never names a URL; every input is a file
# under <assets> or this repository. Environment:
#   RSCRIPT   Rscript of the pinned R (default /opt/R/4.6.2/bin/Rscript)
#   JOBS      parallel build jobs (default: nproc)
#   KEEP_RUST set to 1 to keep the Rust toolchain after the R library is built
#
# Layout of <prefix> (paths are recorded in the venvs and runtime.json, so whatever uses
# the result must see it at the same path):
#   rt/        uv, python, quarto (rt/rust only while building, unless KEEP_RUST=1)
#   envs/      launcher/.venv and tools/.venv, installed from hashed requirements
#   rlib/      the app-private R library (renv.lock versions)
#   runtime/runtime.json   the runtime record the launcher reads (core/runtime.py)
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "usage: $0 <assets> <prefix>" >&2
  exit 2
fi
ASSETS="$(cd "$1" && pwd)"
mkdir -p "$2"
PREFIX="$(cd "$2" && pwd)"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
RSCRIPT="${RSCRIPT:-/opt/R/4.6.2/bin/Rscript}"
JOBS="${JOBS:-$(nproc)}"
RESULTS="${RESULTS:-$PREFIX/results}"
mkdir -p "$RESULTS"

case "$(uname -m)" in
  x86_64) RUST_TARGET=x86_64-unknown-linux-gnu ;;
  aarch64 | arm64) RUST_TARGET=aarch64-unknown-linux-gnu ;;
  *) echo "unsupported architecture: $(uname -m)" >&2; exit 2 ;;
esac

# The one runtime file matching a pattern (fetch.py puts only pinned files in runtime/).
# Plain globs, so no Python is needed before the pinned one is unpacked.
runtime_file() {
  local matches=("$ASSETS"/runtime/$1)
  if [[ ${#matches[@]} -ne 1 || ! -f "${matches[0]}" ]]; then
    echo "expected one $1 in $ASSETS/runtime, found ${#matches[@]}" >&2
    exit 1
  fi
  echo "${matches[0]}"
}

# Nothing may reach for the network (docs/offline.md).
export UV_OFFLINE=1 UV_NO_INDEX=1 UV_FIND_LINKS="$ASSETS/wheelhouse" UV_PYTHON_DOWNLOADS=never
export UV_CACHE_DIR="$PREFIX/.build/uv-cache" UV_PYTHON_INSTALL_DIR="$PREFIX/.build/uv-python" UV_NO_PROGRESS=1
export R_PROFILE_USER=/dev/null R_ENVIRON_USER=/dev/null R_LIBS_USER="$PREFIX/rlib" R_LIBS="$PREFIX/rlib"
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
RT="$PREFIX/rt"
mkdir -p "$RT"
tar --no-same-owner -xzf "$(runtime_file 'uv-*.tar.gz')" -C "$RT"
UV_DIR="$(dirname "$(find "$RT" -maxdepth 2 -name uv -type f | head -1)")"
tar --no-same-owner -xzf "$(runtime_file 'cpython-*.tar.gz')" -C "$RT"   # -> rt/python
tar --no-same-owner -xzf "$(runtime_file 'quarto-*.tar.gz')" -C "$RT"
QUARTO="$(find "$RT" -maxdepth 3 -path '*/bin/quarto' -type f | head -1)"
mkdir -p "$RT/rust-src"
tar --no-same-owner -xJf "$(runtime_file 'rust-*.tar.xz')" -C "$RT/rust-src" --strip-components=1
"$RT/rust-src/install.sh" --prefix="$RT/rust" --disable-ldconfig \
  --components="rustc,cargo,rust-std-$RUST_TARGET" > "$RESULTS/rust-install.log"
rm -rf "$RT/rust-src"
PYTHON="$RT/python/bin/python3"
# The app-owned uv comes first, so pyro and reportifyr find it instead of installing one.
export PATH="$UV_DIR:$(dirname "$QUARTO"):$RT/rust/bin:$(dirname "$RSCRIPT"):/usr/bin:/bin"
uv --version
"$PYTHON" --version
quarto --version
cargo --version
done_step

step python
LAUNCHER_ENV="$PREFIX/envs/launcher/.venv"
TOOLS_ENV="$PREFIX/envs/tools/.venv"
# `uv sync --locked` can't install offline from a wheelhouse: it fetches each locked file by
# its recorded URL and only falls back to uv's cache, never to UV_FIND_LINKS (traced; see
# docs/offline.md I3). Install the requirements exported from the same locks instead, with
# every hash enforced.
uv venv --quiet --python "$PYTHON" "$LAUNCHER_ENV"
uv pip install --python "$LAUNCHER_ENV/bin/python" --require-hashes \
  --requirement "$ASSETS/requirements/launcher.offline.txt"
# opncafyr itself, unless it is installed later (the container image does that in its own
# layer, so a source change doesn't rebuild the R library).
launcher_wheels=("$ASSETS"/wheelhouse/opncafyr-*.whl)
if [[ -f "${launcher_wheels[0]}" ]]; then
  uv pip install --python "$LAUNCHER_ENV/bin/python" --no-deps "${launcher_wheels[@]}"
fi
uv venv --quiet --python "$PYTHON" "$TOOLS_ENV"
uv pip install --python "$TOOLS_ENV/bin/python" --require-hashes \
  --requirement "$ASSETS/requirements/tools.offline.txt"
"$LAUNCHER_ENV/bin/python" -c 'import fastapi, httpx, uvicorn; print("launcher env ok")'
"$TOOLS_ENV/bin/python" -c 'import deckifyr, docx, yaml, PIL, pymupdf; print("tools env ok")'
done_step

step r
"$RSCRIPT" --vanilla "$REPO/scripts/offline/install_r.R" \
  "$REPO/runtime/r/renv.lock" "$ASSETS/rrepo" "$ASSETS/cellar" "$PREFIX/rlib"
done_step

if [[ "${KEEP_RUST:-0}" != 1 ]]; then
  rm -rf "$RT/rust"
fi
rm -rf "$PREFIX/.build"

# The runtime record the installer would write, naming the pinned executables. Optional
# executables (git, soffice) are added when they exist at the given paths.
mkdir -p "$PREFIX/runtime"
"$PYTHON" - "$PREFIX/runtime/runtime.json" "$RSCRIPT" "$QUARTO" "$UV_DIR/uv" \
  "$TOOLS_ENV/bin/python" "$PREFIX/rlib" "${GIT_EXE:-}" "${SOFFICE_EXE:-}" <<'PY'
import json, os, sys
out, rscript, quarto, uv, tools_python, rlib, git, soffice = sys.argv[1:]
executables = {"rscript": rscript, "quarto": quarto, "uv": uv, "deckifyr_python": tools_python}
for name, path in (("git", git), ("soffice", soffice)):
    if path and os.path.isfile(path):
        executables[name] = path
json.dump(
    {"schema_version": 1, "executables": executables, "r_library": rlib},
    open(out, "w"),
    indent=2,
)
PY
echo "runtime installed in $PREFIX"
