#!/usr/bin/env bash
# CI (#19, #36): the installer end to end on a macOS runner, as a new standard user.
#
#   sudo is used for runner setup only (hide the preinstalled R, create the user). Every
#   installer step runs as that user, without sudo.
#
#   scripts/installer_ci/macos.sh <payload dir> <results dir> <newer payload dir>
#   env: GH_TOKEN  read access to the private opnca-r release (the online token path)
#
# #19 legs: install (online) -> second run is a no-op -> the launcher answers /health ->
# a failure injected after staging keeps the previous install and writes a support bundle
# -> uninstall removes the app, R and the icon but not a workspace.
# #36 legs: build the bundle twice from independent downloads and compare the manifests
# -> install from the bundle with outbound network denied (sandbox-exec) and no denial in
# the sandbox log -> the Gate 2 flow on that install, still denied (#51) -> a tampered
# bundle is refused and the old install still runs -> an
# update to a newer payload killed mid-way leaves the old install runnable -> the offline
# update completes -> the installation report is there -> uninstall.
set -euo pipefail

PAYLOAD_SRC="$(cd "$1" && pwd)"
RESULTS="$2"
NEWER_SRC="$(cd "$3" && pwd)"
mkdir -p "$RESULTS"
RESULTS="$(cd "$RESULTS" && pwd)"
U=opncafyrci
HOMEDIR="/Users/$U"
WORK=/Users/Shared/opncafyr-ci
ROOT="$HOMEDIR/Library/Application Support/opncafyr"
RROOT="$HOMEDIR/Library/opncafyr"
APP="$HOMEDIR/Applications/opncafyr.app"
case "$(uname -m)" in arm64) PLATFORM=macos-arm64 ;; *) PLATFORM=macos-x86_64 ;; esac

fail() { echo "::error::$*"; exit 1; }

# ---- runner setup (sudo) ---------------------------------------------------------------
if [ -e /Library/Frameworks/R.framework ]; then
  sudo mv /Library/Frameworks/R.framework /Library/Frameworks/R.framework.hidden
fi
for b in R Rscript quarto; do
  p="$(command -v "$b" || true)"
  if [ -n "$p" ]; then sudo mv "$p" "$p.hidden"; fi
done
if ! id "$U" >/dev/null 2>&1; then
  sudo sysadminctl -addUser "$U" -fullName "opncafyr CI" -password "$(openssl rand -hex 16)" -home "$HOMEDIR"
  sudo createhomedir -c -u "$U" >/dev/null
fi
if dseditgroup -o checkmember -m "$U" admin | grep -q "is a member"; then fail "$U is an admin"; fi
sudo rm -rf "$WORK"
sudo mkdir -p "$WORK"
sudo cp -R "$PAYLOAD_SRC" "$WORK/payload"
sudo cp -R "$NEWER_SRC" "$WORK/payload-newer"
sudo cp "$(dirname "$0")/../spikes/nonet.sb" "$WORK/nonet.sb"
# The Gate 2 flow driver (#51) and the fixture helpers it imports, in the repository layout.
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
sudo mkdir -p "$WORK/flow/scripts/offline" "$WORK/flow/tests/fixtures"
sudo cp "$REPO/scripts/offline/flow_check.py" "$WORK/flow/scripts/offline/"
sudo cp "$REPO"/tests/{nca_helpers,publish_helpers,artifact_docs}.py "$WORK/flow/tests/"
sudo cp "$REPO"/tests/fixtures/two_analyte.{csv,expected.json} "$WORK/flow/tests/fixtures/"
sudo chown -R "$U" "$WORK"

as_user() {
  sudo -u "$U" -H env -i HOME="$HOMEDIR" USER="$U" LOGNAME="$U" PATH=/usr/bin:/bin:/usr/sbin:/sbin \
    ${GH_TOKEN:+GH_TOKEN="$GH_TOKEN"} ${FAIL_AFTER:+OPNCAFYR_INSTALL_FAIL_AFTER="$FAIL_AFTER"} "$@"
}
# As the user, outbound network denied except loopback (scripts/spikes/nonet.sb). The
# installer's closed loopback proxy is off, so any attempt reaches the deny rule and is
# logged by the sandbox, which no_attempts_since checks.
offline_user() {
  sudo -u "$U" -H env -i HOME="$HOMEDIR" USER="$U" LOGNAME="$U" PATH=/usr/bin:/bin:/usr/sbin:/sbin \
    OPNCAFYR_PROXY_TRAP=0 sandbox-exec -f "$WORK/nonet.sb" "$@"
}
no_attempts_since() {  # no_attempts_since <"YYYY-mm-dd HH:MM:SS">
  local denied
  denied="$(/usr/bin/log show --start "$1" --style compact \
    --predicate 'sender == "Sandbox" AND eventMessage CONTAINS "network-outbound"' 2>/dev/null | grep deny || true)"
  if [ -n "$denied" ]; then
    echo "$denied" | tee -a "$RESULTS/network-attempts.log"
    fail "connection attempts were made with the network blocked (see network-attempts.log)"
  fi
  echo "no connection attempts since $1"
}
leg() { echo; echo "::group::$1"; }
endleg() { echo "::endgroup::"; }
active() { sudo readlink "$ROOT/current" 2>/dev/null || true; }
health() {  # health <port>: start the installed launcher as the user, check /health, stop it
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

# ---- 1. online install ---------------------------------------------------------------
leg "online install"
as_user bash "$WORK/payload/install.sh" install 2>&1 | tee "$RESULTS/1-install.log"
[ "${PIPESTATUS[0]}" -eq 0 ] || fail "install failed"
[ -n "$(active)" ] || fail "nothing is active"
sudo test -x "$APP/Contents/MacOS/opncafyr" || fail "no .app"
sudo grep -q "$ROOT" "$APP/Contents/MacOS/opncafyr" || fail ".app does not name the app root"
sudo test -x "$RROOT/versions/$(basename "$(active)")/R.framework/Resources/bin/R" || fail "no R"
test ! -e /Library/Frameworks/R.framework || fail "something wrote /Library/Frameworks/R.framework"
as_user bash "$WORK/payload/install.sh" status
endleg

# ---- 2. second run is a no-op ------------------------------------------------------------
leg "second run"
before="$(active)"
as_user bash "$WORK/payload/install.sh" install 2>&1 | tee "$RESULTS/2-noop.log"
grep -q "is already installed" "$RESULTS/2-noop.log" || fail "second run was not a no-op"
[ "$(active)" = "$before" ] || fail "second run changed the active install"
endleg

# ---- 3. the launcher the icon starts answers /health -------------------------------------
leg "launch"
health 47801
endleg

# ---- 4. failure after staging keeps the previous install ---------------------------------
leg "failure after staging"
before="$(active)"
set +e
FAIL_AFTER=staging as_user bash "$WORK/payload/install.sh" install --reinstall 2>&1 | tee "$RESULTS/4-failure.log"
rc=${PIPESTATUS[0]}
set -e
[ "$rc" -eq 1 ] || fail "the injected failure exited $rc, not 1"
[ "$(active)" = "$before" ] || fail "the active install changed after a failed install"
sudo ls "$ROOT/logs/support/" | grep -q '^install-failure-.*\.zip$' || fail "no support bundle"
sudo sh -c 'cp "$1"/logs/support/*.zip "$2"/' _ "$ROOT" "$RESULTS"
grep -q "Previous install still active" "$RESULTS/4-failure.log" || fail "no rollback message"
health 47802
endleg

# ---- 5. uninstall: app files, R and the icon go; a workspace stays -----------------------
leg "uninstall"
as_user mkdir -p "$HOMEDIR/study/.opncafyr"
as_user sh -c "printf 'a,b\n1,2\n' > '$HOMEDIR/study/data.csv'; echo '{}' > '$HOMEDIR/study/.opncafyr/workspace.json'"
sudo cp -R "$ROOT/logs" "$RESULTS/app-logs" || true
as_user bash "$ROOT/installer/install.sh" uninstall 2>&1 | tee "$RESULTS/5-uninstall.log"
[ "${PIPESTATUS[0]}" -eq 0 ] || fail "uninstall failed"
sudo test ! -e "$ROOT" || fail "app root still exists"
sudo test ! -e "$RROOT" || fail "R root still exists"
sudo test ! -e "$APP" || fail "icon still exists"
sudo test -f "$HOMEDIR/study/data.csv" || fail "the workspace was touched"
sudo test -f "$HOMEDIR/study/.opncafyr/workspace.json" || fail "the workspace metadata was touched"
endleg

# ---- 6. bundle builder: two independent builds list the same files and hashes ----------
leg "bundle builder"
B1="$RESULTS/../bundle-build-1"; B2="$RESULTS/../bundle-build-2"   # the runner's own folders
rm -rf "$B1" "$B2"; mkdir -p "$B1" "$B2"
B1="$(cd "$B1" && pwd)"; B2="$(cd "$B2" && pwd)"
OPNCAFYR_APP_ROOT="$B1/cache" bash "$WORK/payload/install.sh" bundle --out "$B1" --archive 2>&1 | tail -3
OPNCAFYR_APP_ROOT="$B2/cache" bash "$WORK/payload/install.sh" bundle --out "$B2" 2>&1 | tail -3
NAME="opncafyr-bundle-$(cat "$WORK/payload/payload.json" | python3 -c 'import json,sys; print(json.load(sys.stdin)["version"])')-$PLATFORM"
cmp "$B1/$NAME/bundle.json" "$B2/$NAME/bundle.json" || fail "two builds of the bundle differ"
(cd "$B1/$NAME" && shasum -a 256 -c SHA256SUMS --quiet) || fail "SHA256SUMS does not verify"
cp "$B1/$NAME/bundle.json" "$B1/$NAME/licenses.json" "$B1/$NAME/sbom.spdx.json" "$RESULTS/"
BUNDLE_ID="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["bundle_id"])' "$B1/$NAME/bundle.json")"
# Carried across to the user, as an IT department would.
sudo mkdir -p "$WORK/bundle"
sudo cp "$B1/$NAME.zip" "$WORK/bundle/"
sudo cp -R "$B1/$NAME" "$WORK/bundle/"
sudo chown -R "$U" "$WORK/bundle"
# The zip is uploaded as a workflow artifact.
mkdir -p "$RESULTS/../bundle-artifact"
mv "$B1/$NAME.zip" "$RESULTS/../bundle-artifact/"
rm -rf "$B1" "$B2"
BUNDLE_ZIP="$WORK/bundle/$NAME.zip"
echo "bundle $BUNDLE_ID: $(du -h "$BUNDLE_ZIP" | cut -f1)"
endleg

# ---- 7. offline install from the bundle, network denied ----------------------------------
leg "offline install from the bundle"
start="$(date '+%Y-%m-%d %H:%M:%S')"
offline_user bash "$WORK/payload/install.sh" install --from-bundle "$BUNDLE_ZIP" 2>&1 | tee "$RESULTS/7-offline.log"
[ "${PIPESTATUS[0]}" -eq 0 ] || fail "offline install failed"
grep -q "(bundle)" "$RESULTS/7-offline.log" || fail "not a bundle install"
report="$(sudo sh -c 'ls -t "$1"/state/reports/install-report-*.json | head -1' _ "$ROOT")"
sudo cp "$report" "$RESULTS/report-offline.json"
sudo cp "${report%.json}.html" "$RESULTS/report-offline.html"
sudo chown "$(id -u)" "$RESULTS/report-offline.json" "$RESULTS/report-offline.html"
python3 - "$RESULTS/report-offline.json" "$BUNDLE_ID" <<'PY'
import json, sys
r = json.load(open(sys.argv[1]))
assert r["result"] == "pass", r["smoke"]
assert r["source"] == "bundle" and r["bundle"]["bundle_id"] == sys.argv[2], r["bundle"]
names = {s["name"] for s in r["smoke"]}
assert {"opnca_calculation", "quarto_docx_render", "deckifyr_build"} <= names, names
assert r["signature"]["checked"] is False and r["payload_digest"]
print("report ok:", r["install_id"], r["system"])
PY
no_attempts_since "$start"
health 47803
endleg

# ---- 7b. the Gate 2 flow on the bundle install, network denied (#51) ---------------------
# The installed launcher's own Python, app root and runtime: import -> clean -> map -> one
# run per analyte saved in opnca-r, replayed and completed -> publish -> report -> deck.
leg "Gate 2 flow on the bundle install"
start="$(date '+%Y-%m-%d %H:%M:%S')"
set +e
offline_user "$ROOT/current/launcher/bin/python" "$WORK/flow/scripts/offline/flow_check.py" \
  "$HOMEDIR/flow" --results "$WORK/flow-results" 2>&1 | tee "$RESULTS/7b-flow.log"
rc=${PIPESTATUS[0]}
set -e
sudo cp -R "$WORK/flow-results" "$RESULTS/flow" || true
sudo cp -R "$ROOT/logs" "$RESULTS/flow/app-logs" || true   # the opnca-r/deckifyr child logs
sudo chown -R "$(id -u)" "$RESULTS/flow" || true
[ "$rc" -eq 0 ] || fail "the Gate 2 flow failed on the bundle install"
grep -q '"opnca": true' "$RESULTS/7b-flow.log" || fail "the flow ran without opnca-r"
no_attempts_since "$start"
endleg

# ---- 8. a tampered bundle is refused; the old install stays ------------------------------
leg "tampered bundle"
before="$(active)"
T="$WORK/tampered"
sudo rm -rf "$T"; sudo cp -R "$WORK/bundle/$NAME" "$T"; sudo chown -R "$U" "$T"
victim="$(sudo sh -c 'ls "$1"/assets/quarto/*' _ "$T")"
printf 'x' | sudo -u "$U" tee -a "$victim" > /dev/null
sudo rm -rf "$ROOT/cache/downloads"   # nothing verified earlier may stand in for it
set +e
offline_user bash "$WORK/payload-newer/install.sh" install --from-bundle "$T" 2>&1 | tee "$RESULTS/8-tampered.log"
rc=${PIPESTATUS[0]}
set -e
[ "$rc" -eq 1 ] || fail "a tampered bundle was not refused (exit $rc)"
grep -q "in: quarto" "$RESULTS/8-tampered.log" || fail "the error does not name the quarto component"
[ "$(active)" = "$before" ] || fail "the active install changed"
health 47804
endleg

# ---- 9. an interrupted offline update leaves the old install runnable --------------------
leg "interrupted update"
before="$(active)"
offline_user bash "$WORK/payload-newer/install.sh" install --from-bundle "$BUNDLE_ZIP" \
  > "$RESULTS/9-interrupted.log" 2>&1 &
for i in $(seq 1 300); do grep -q '^\[r_library\]' "$RESULTS/9-interrupted.log" && break; sleep 1; done
grep -q '^\[r_library\]' "$RESULTS/9-interrupted.log" || fail "the update never reached r_library"
sudo pkill -9 -u "$U" || true   # everything the user runs: the installer and its R children
wait || true
[ "$(active)" = "$before" ] || fail "the active install changed after an interrupted update"
health 47805
endleg

# ---- 10. the offline update completes ------------------------------------------------------
leg "offline update"
start="$(date '+%Y-%m-%d %H:%M:%S')"
offline_user bash "$WORK/payload-newer/install.sh" install --from-bundle "$BUNDLE_ZIP" 2>&1 | tee "$RESULTS/10-update.log"
[ "${PIPESTATUS[0]}" -eq 0 ] || fail "offline update failed"
grep -q "removing incomplete install" "$RESULTS/10-update.log" || fail "the interrupted install was not cleaned up"
newer="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["version"])' "$WORK/payload-newer/payload.json")"
case "$(active)" in versions/"$newer"-*) ;; *) fail "the update is not active: $(active)" ;; esac
no_attempts_since "$start"
health 47806
endleg

# ---- 11. uninstall ---------------------------------------------------------------------------
leg "uninstall after the update"
sudo cp -R "$ROOT/logs" "$RESULTS/app-logs-offline" || true
as_user bash "$ROOT/installer/install.sh" uninstall 2>&1 | tee "$RESULTS/11-uninstall.log"
sudo test ! -e "$ROOT" || fail "app root still exists"
sudo test ! -e "$RROOT" || fail "R root still exists"
endleg

sudo chown -R "$(id -u)" "$RESULTS"
echo "installer CI: all legs passed"
