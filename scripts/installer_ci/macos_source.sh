#!/usr/bin/env bash
# CI (#34): `repair --build-from-source` on a macOS runner, as a new standard user, offline.
#
#   sudo is used for runner setup only (hide the preinstalled R, create the user). Every
#   installer step runs as that user, without sudo.
#
#   scripts/installer_ci/macos_source.sh <payload dir> <results dir>
#   env: GH_TOKEN  read access to the private opnca-r release
#
# Legs: online binary install -> a decoy `opnca` 0.0.1 is planted in the user's own R
# library (its .onLoad leaves a marker) -> a bundle with the source-build files is built
# -> `repair --build-from-source --from-bundle` with outbound network denied
# (sandbox-exec) -> the new install records the source build, lists the vendored tarball,
# passes its smoke tests (including the R library isolation check) and answers /health;
# the decoy was never loaded, the user's R library is unchanged, no ~/.cargo or ~/.rustup
# was made, no build folder is left, and no connection was attempted -> uninstall.
#
# GitHub's macOS runners have Xcode, which provides the compiler the build needs. A real
# standard user needs Apple's Command Line Tools for it, which need an administrator; the
# installer's preflight says so (tests/test_source_build.py).
set -euo pipefail

PAYLOAD_SRC="$(cd "$1" && pwd)"
RESULTS="$2"
mkdir -p "$RESULTS"
RESULTS="$(cd "$RESULTS" && pwd)"
U=opncafyrsrc
HOMEDIR="/Users/$U"
WORK=/Users/Shared/opncafyr-src
ROOT="$HOMEDIR/Library/Application Support/opncafyr"
case "$(uname -m)" in arm64) PLATFORM=macos-arm64; RARCH=arm64 ;; *) PLATFORM=macos-x86_64; RARCH=x86_64 ;; esac
USERLIB="$HOMEDIR/Library/R/$RARCH/4.6/library"

fail() { echo "::error::$*"; exit 1; }
leg() { echo; echo "::group::$1"; }
endleg() { echo "::endgroup::"; }

# ---- runner setup (sudo) ---------------------------------------------------------------
if [ -e /Library/Frameworks/R.framework ]; then
  sudo mv /Library/Frameworks/R.framework /Library/Frameworks/R.framework.hidden
fi
for b in R Rscript quarto cargo rustc; do
  p="$(command -v "$b" || true)"
  if [ -n "$p" ]; then sudo mv "$p" "$p.hidden"; fi
done
if ! id "$U" >/dev/null 2>&1; then
  sudo sysadminctl -addUser "$U" -fullName "opncafyr source CI" -password "$(openssl rand -hex 16)" -home "$HOMEDIR"
  sudo createhomedir -c -u "$U" >/dev/null
fi
if dseditgroup -o checkmember -m "$U" admin | grep -q "is a member"; then fail "$U is an admin"; fi
sudo rm -rf "$WORK"
sudo mkdir -p "$WORK"
sudo cp -R "$PAYLOAD_SRC" "$WORK/payload"
sudo cp "$(dirname "$0")/../spikes/nonet.sb" "$WORK/nonet.sb"
sudo chown -R "$U" "$WORK"

as_user() {
  sudo -u "$U" -H env -i HOME="$HOMEDIR" USER="$U" LOGNAME="$U" PATH=/usr/bin:/bin:/usr/sbin:/sbin \
    ${GH_TOKEN:+GH_TOKEN="$GH_TOKEN"} "$@"
}
offline_user() {
  sudo -u "$U" -H env -i HOME="$HOMEDIR" USER="$U" LOGNAME="$U" PATH=/usr/bin:/bin:/usr/sbin:/sbin \
    OPNCAFYR_PROXY_TRAP=0 sandbox-exec -f "$WORK/nonet.sb" "$@"
}
no_attempts_since() {
  local denied
  denied="$(/usr/bin/log show --start "$1" --style compact \
    --predicate 'sender == "Sandbox" AND eventMessage CONTAINS "network-outbound"' 2>/dev/null | grep deny || true)"
  if [ -n "$denied" ]; then
    echo "$denied" | tee -a "$RESULTS/network-attempts.log"
    fail "connection attempts were made with the network blocked (see network-attempts.log)"
  fi
  echo "no connection attempts since $1"
}
active() { sudo readlink "$ROOT/current" 2>/dev/null || true; }
health() {
  local port="$1" i body=""
  as_user "$ROOT/current/launcher/bin/python" -m opncafyr launch --no-browser --port "$port" \
    > "$RESULTS/launch-$port.log" 2>&1 &
  for i in $(seq 1 60); do
    body="$(curl -fsS "http://127.0.0.1:$port/health" 2>/dev/null || true)"
    [ -n "$body" ] && break
    sleep 1
  done
  sudo pkill -u "$U" -f "opncafyr launch --no-browser --port $port" || true
  sleep 1
  echo "health: $body"
  echo "$body" | grep -q '"status":"ok"' || fail "launcher did not answer /health"
}
userlib_state() {  # every file in the user's R library with its size and hash
  sudo sh -c 'cd "$1" && find . -type f -exec shasum -a 256 {} + | sort' _ "$USERLIB"
}

# ---- 1. online binary install ------------------------------------------------------------
leg "online install (binary)"
as_user bash "$WORK/payload/install.sh" install 2>&1 | tee "$RESULTS/1-install.log"
[ "${PIPESTATUS[0]}" -eq 0 ] || fail "install failed"
binary_install="$(basename "$(active)")"
[ -n "$binary_install" ] || fail "nothing is active"
endleg

# ---- 2. a decoy opnca in the user's own R library ------------------------------------------
leg "decoy package in the user's R library"
D="$WORK/decoy/opnca"
as_user mkdir -p "$D/R" "$USERLIB"
as_user sh -c "printf 'Package: opnca\nVersion: 0.0.1\nTitle: Decoy\nDescription: Must never load.\nLicense: MIT\nAuthor: CI\nMaintainer: CI <ci@example.invalid>\n' > '$D/DESCRIPTION'"
as_user sh -c "printf 'export(decoy)\n' > '$D/NAMESPACE'"
as_user sh -c "printf '.onLoad <- function(lib, pkg) writeLines(\"loaded\", file.path(Sys.getenv(\"HOME\"), \"opnca-decoy-loaded\"))\ndecoy <- function() TRUE\n' > '$D/R/zzz.R'"
as_user env R_LIBS_USER="$USERLIB" "$ROOT/current/bin/R" CMD INSTALL --no-test-load "--library=$USERLIB" "$D" \
  > "$RESULTS/2-decoy.log" 2>&1 || fail "could not plant the decoy"
sudo test -f "$USERLIB/opnca/DESCRIPTION" || fail "no decoy in $USERLIB"
userlib_state > "$RESULTS/userlib-before.txt"
sudo test ! -e "$HOMEDIR/opnca-decoy-loaded" || fail "the decoy loaded while planting it"
endleg

# ---- 3. a bundle that carries the source-build files ---------------------------------------
leg "bundle with the source build"
B="$RESULTS/../bundle-source"
rm -rf "$B"; mkdir -p "$B"; B="$(cd "$B" && pwd)"
OPNCAFYR_APP_ROOT="$B/cache" bash "$WORK/payload/install.sh" bundle --out "$B" --with-source-build 2>&1 | tail -3
NAME="opncafyr-bundle-$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["version"])' "$WORK/payload/payload.json")-$PLATFORM"
python3 - "$B/$NAME/bundle.json" <<'PY'
import json, sys
b = json.load(open(sys.argv[1]))
assert b["options"]["source_build"] is True, b["options"]
files = {f["path"].rsplit("/", 1)[-1] for f in b["files"]}
assert any(f.endswith("-vendored.tar.gz") for f in files), "no vendored tarball"
assert any(f.startswith("rust-") for f in files), "no Rust toolchain"
print("bundle carries the source build:", sorted(f for f in files if "vendored" in f or f.startswith("rust-")))
PY
sudo mkdir -p "$WORK/bundle"
sudo cp -R "$B/$NAME" "$WORK/bundle/"
sudo chown -R "$U" "$WORK/bundle"
rm -rf "$B"
endleg

# ---- 4. repair --build-from-source, offline ----------------------------------------------
leg "repair --build-from-source (network denied)"
start="$(date '+%Y-%m-%d %H:%M:%S')"
set +e
offline_user bash "$WORK/payload/install.sh" repair --build-from-source --from-bundle "$WORK/bundle/$NAME" \
  2>&1 | tee "$RESULTS/4-source.log"
rc=${PIPESTATUS[0]}
set -e
sudo cp -R "$ROOT/logs" "$RESULTS/app-logs" || true
sudo chown -R "$(id -u)" "$RESULTS/app-logs" || true
[ "$rc" -eq 0 ] || fail "repair --build-from-source failed (exit $rc)"
source_install="$(basename "$(active)")"
[ "$source_install" != "$binary_install" ] || fail "the repair did not stage a new install"
sudo cat "$ROOT/versions/$source_install/install.json" > "$RESULTS/install-source.json"
python3 - "$RESULTS/install-source.json" <<'PY'
import json, sys
r = json.load(open(sys.argv[1]))
assert r["options"]["build_from_source"] is True, r["options"]
opnca = next(c for c in r["components"] if c["name"] == "opnca-r")
assert any(f["file"].endswith("-vendored.tar.gz") for f in opnca["files"]), opnca
assert any(c["name"] == "rust" for c in r["components"]), "rust not recorded"
smoke = {s["name"]: s for s in r["smoke"]}
for name in ("r_packages", "r_library_isolation", "opnca_calculation"):
    assert smoke[name]["status"] == "ok", smoke[name]
print("source install:", r["install_id"], smoke["r_library_isolation"]["detail"])
PY
no_attempts_since "$start"
endleg

# ---- 5. the user's own R was left alone ----------------------------------------------------
leg "the user's R library and home"
sudo test ! -e "$HOMEDIR/opnca-decoy-loaded" || fail "the decoy opnca in the user's library was loaded"
userlib_state > "$RESULTS/userlib-after.txt"
cmp "$RESULTS/userlib-before.txt" "$RESULTS/userlib-after.txt" || fail "the user's R library changed"
sudo test ! -e "$HOMEDIR/.cargo" || fail "~/.cargo was created"
sudo test ! -e "$HOMEDIR/.rustup" || fail "~/.rustup was created"
tmp="$(sudo -u "$U" getconf DARWIN_USER_TEMP_DIR)"
if sudo sh -c 'ls -d "$1"/opncafyr-install-*/source-build 2>/dev/null' _ "$tmp" | grep -q .; then
  fail "a source-build folder was left behind"
fi
health 47821
endleg

# ---- 6. uninstall ------------------------------------------------------------------------
leg "uninstall"
as_user bash "$ROOT/installer/install.sh" uninstall 2>&1 | tee "$RESULTS/6-uninstall.log"
[ "${PIPESTATUS[0]}" -eq 0 ] || fail "uninstall failed"
sudo test -f "$USERLIB/opnca/DESCRIPTION" || fail "uninstall touched the user's R library"
endleg
echo "source build legs passed"
