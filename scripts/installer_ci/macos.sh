#!/usr/bin/env bash
# CI (#19): installer v1 end to end on a macOS runner, as a new standard user.
#
#   sudo is used for runner setup only (hide the preinstalled R, create the user). Every
#   installer step runs as that user, without sudo.
#
#   scripts/installer_ci/macos.sh <payload dir> <results dir>
#   env: GH_TOKEN  read access to the private opnca-r release (the online token path)
#
# Legs: install (online) -> second run is a no-op -> the launcher answers /health ->
# a failure injected after staging keeps the previous install and writes a support bundle
# -> uninstall removes the app, R and the icon but not a workspace.
set -euo pipefail

PAYLOAD_SRC="$(cd "$1" && pwd)"
RESULTS="$2"
mkdir -p "$RESULTS"
RESULTS="$(cd "$RESULTS" && pwd)"
U=opncafyrci
HOMEDIR="/Users/$U"
WORK=/Users/Shared/opncafyr-ci
ROOT="$HOMEDIR/Library/Application Support/opncafyr"
RROOT="$HOMEDIR/Library/opncafyr"
APP="$HOMEDIR/Applications/opncafyr.app"

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
sudo chown -R "$U" "$WORK"

as_user() {
  sudo -u "$U" -H env -i HOME="$HOMEDIR" USER="$U" LOGNAME="$U" PATH=/usr/bin:/bin:/usr/sbin:/sbin \
    ${GH_TOKEN:+GH_TOKEN="$GH_TOKEN"} ${FAIL_AFTER:+OPNCAFYR_INSTALL_FAIL_AFTER="$FAIL_AFTER"} "$@"
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

sudo chown -R "$(id -u)" "$RESULTS"
echo "installer CI: all legs passed"
