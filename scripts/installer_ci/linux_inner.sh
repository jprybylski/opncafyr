#!/usr/bin/env bash
# Runs inside the minimal Ubuntu 24.04 container as an unprivileged user, with no network
# (scripts/installer_ci/linux.sh, #65). /b is the bundle (read-only), /newer a bundle of a later
# payload, /flowrepo the flow driver, /results is writable. Every installer command runs under strace so that a connection
# attempt is visible even if a tool swallows the failure.
set -euo pipefail

BASE_VERSION="$1"
export PATH=/usr/bin:/bin
ROOT="$HOME/.local/share/opncafyr"
INSTALL="/b/installer/install.sh"
TR=/results
STEP=0
fail() { echo "::error::$*"; exit 1; }
traced() { # <name> <command...>: run under strace, keep the log
  local name="$1"; shift
  STEP=$((STEP + 1))
  strace -f -qq -s 200 -e trace=%network,%process -o "$TR/trace-$STEP-$name.txt" "$@"
}
active() { readlink "$ROOT/current" | sed 's#versions/##'; }

echo "== the machine"
[ "$(id -u)" -ne 0 ] || fail "running as root"
for c in gcc cc make rustc cargo sudo; do ! command -v "$c" >/dev/null || fail "$c is present"; done
echo "user $(id -un), no compiler, no make, no sudo; glibc $(getconf GNU_LIBC_VERSION)"

echo "== install from the bundle"
traced install "$INSTALL" install --from-bundle /b
FIRST="$(active)"
[ -n "$FIRST" ] || fail "no active install"
echo "active: $FIRST"

echo "== a second install is a no-op"
traced noop "$INSTALL" install --from-bundle /b | tee "$TR/noop.log"
grep -q "already installed" "$TR/noop.log" || fail "the second install was not a no-op"

echo "== repair finds nothing to fix"
traced repair "$INSTALL" repair --from-bundle /b | tee "$TR/repair.log"
grep -q "nothing to repair" "$TR/repair.log" || fail "repair"

echo "== the installed runtime works as this user"
PY="$ROOT/current/launcher/bin/python"
"$PY" -I -c 'import opncafyr, sys; print("launcher", opncafyr.__version__)'
"$ROOT/bin/opncafyr" doctor | tee "$TR/doctor.log" | head -20
grep -q "rscript" "$TR/doctor.log" && fail "doctor says the runtime has no Rscript"
grep -q "tool opnca .*unavailable" "$TR/doctor.log" && fail "doctor says opnca-r is unavailable"
grep -q "git " "$TR/doctor.log" || true

echo "== the Gate 2 flow on this install: two real opnca-r runs, replay, publish, DOCX report, PPTX deck"
mkdir -p /tmp/flow
traced flow env OPNCAFYR_RUNTIME_FILE="$ROOT/current/runtime/runtime.json" \
  "$PY" -I /flowrepo/scripts/offline/flow_check.py /tmp/flow/w --results "$TR" | tee "$TR/flow.log"
grep -q '^flow complete' "$TR/flow.log" || fail "the flow did not complete"
grep -q '"replay": "passed"' "$TR/flow.log" || fail "a replay did not pass"
! grep -q 'stand-in' "$TR/flow.log" || fail "the flow used stand-in runs: opnca-r did not run"

echo "== a native launch is reachable through its forward and refuses foreign callers"
mkdir -p /tmp/study
"$ROOT/bin/opncafyr" launch --remote --port 8765 --local-port 9100 --workspace /tmp/study \
  >"$TR/launch.out" 2>"$TR/launch.log" &
LAUNCH=$!
for _ in $(seq 1 60); do grep -q '^Open: ' "$TR/launch.log" && break; sleep 0.5; done
grep -q '^Open: http://127.0.0.1:9100/open' "$TR/launch.log" || { cat "$TR/launch.log"; fail "no one-time link"; }
"$PY" -I - <<'PYEOF'
import http.client, json
def get(path, host, method="GET", headers=None, body=None):
    c = http.client.HTTPConnection("127.0.0.1", 8765, timeout=10)
    c.putrequest(method, path, skip_host=True)
    c.putheader("Host", host)
    for k, v in (headers or {}).items():
        c.putheader(k, v)
    if body is not None:
        c.putheader("Content-Type", "application/json"); c.putheader("Content-Length", str(len(body)))
    c.endheaders(body)
    r = c.getresponse(); r.read(); return r.status
assert get("/health", "127.0.0.1:9100") == 200, "the forwarded host is accepted"
assert get("/health", "evil.example") == 400, "a foreign Host is refused"
body = json.dumps({"path": "/tmp/study"}).encode()
assert get("/api/workspace/inspect", "127.0.0.1:9100", "POST", {"Origin": "http://127.0.0.1:9100"}, body) == 403, "no session secret"
assert get("/api/workspace/inspect", "127.0.0.1:9100", "POST", {"Origin": "https://evil.example"}, body) == 403, "foreign Origin"
print("health 200; foreign Host 400; missing secret 403; foreign Origin 403")
PYEOF
kill "$LAUNCH"; wait "$LAUNCH" 2>/dev/null || true

echo "== a tampered asset is refused and the old install still runs"
cp -a /b /tmp/bundle-bad
QF="$(ls /tmp/bundle-bad/assets/quarto/* | head -1)"
printf 'x' >>"$QF"
if traced tamper "$INSTALL" install --reinstall --from-bundle /tmp/bundle-bad >"$TR/tamper.log" 2>&1; then
  fail "a tampered Quarto archive was accepted"
fi
grep -qi "does not match\|sha-256\|doesn't match" "$TR/tamper.log" || { cat "$TR/tamper.log"; fail "no checksum message"; }
[ "$(active)" = "$FIRST" ] || fail "the active install changed after a refused bundle"
"$PY" -I -c 'import opncafyr' || fail "the previous install no longer runs"
rm -rf /tmp/bundle-bad

echo "== a missing asset is refused"
cp -a /b /tmp/bundle-missing
rm -f "$(ls /tmp/bundle-missing/assets/uv/* | head -1)"
if traced missing "$INSTALL" install --reinstall --from-bundle /tmp/bundle-missing >"$TR/missing.log" 2>&1; then
  fail "a bundle without uv was accepted"
fi
[ "$(active)" = "$FIRST" ] || fail "the active install changed after a bundle with a missing file"
rm -rf /tmp/bundle-missing

echo "== an update killed part-way keeps the old install runnable"
NEWER_INSTALL=/newer/installer/install.sh
if OPNCAFYR_INSTALL_FAIL_AFTER=r_library traced killed "$NEWER_INSTALL" install --from-bundle /newer >"$TR/killed.log" 2>&1; then
  fail "the simulated failure did not fail the update"
fi
[ "$(active)" = "$FIRST" ] || fail "a failed update changed the active install"
"$PY" -I -c 'import opncafyr' || fail "the old install does not run after a failed update"

echo "== the update completes"
traced update "$NEWER_INSTALL" install --from-bundle /newer
SECOND="$(active)"
[ "$SECOND" != "$FIRST" ] || fail "the update did not activate a new install"
"$ROOT/bin/opncafyr" doctor >/dev/null
compgen -G "$ROOT/state/reports/install-report-*.json" >/dev/null || fail "no installation report"
echo "updated $FIRST -> $SECOND"

echo "== uninstall removes the app and leaves a workspace"
touch /tmp/study/keep.csv
traced uninstall "$ROOT/installer/install.sh" uninstall >"$TR/uninstall.log" 2>&1 || { cat "$TR/uninstall.log"; fail "uninstall failed"; }
[ ! -e "$ROOT" ] || fail "the app folder is still there"
[ -f /tmp/study/keep.csv ] || fail "uninstall touched a workspace"
echo "== all legs passed"
