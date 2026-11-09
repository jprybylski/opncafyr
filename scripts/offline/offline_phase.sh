#!/usr/bin/env bash
# Offline phase of the offline CI job (#44): install from local assets, then run the flow.
#
#   scripts/offline/offline_phase.sh <assets dir from fetch.py> <work dir>
#
# Run it with the network cut (the CI job uses a network namespace and strace; see
# docs/offline.md). It never names a URL: every input is a file under <assets> or this
# repository. Environment: RSCRIPT and JOBS, passed on to install_runtime.sh.
#
# Steps (each prints "== step" and its duration; any failure stops the run):
#   1-3. runtimes, python, r: scripts/offline/install_runtime.sh into <work>/install, the
#      same install the container image runs (#42)
#   4. flow: the Gate 2 flow through the launcher (scripts/offline/flow_check.py): import
#      the two-analyte fixture, clean, map, one run per analyte saved in opnca-r, replayed
#      and completed, then publish, render the DOCX report and build the PPTX deck. Every
#      tool process is started by the launcher's adapters, which apply the offline
#      settings themselves: the driver runs with a clean environment, so none of the
#      install's variables reach it. Without opnca-r (no release token) the runs stop at
#      the snapshot and stand-in runs are published.
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "usage: $0 <assets> <work>" >&2
  exit 2
fi
ASSETS="$(cd "$1" && pwd)"
mkdir -p "$2"
WORK="$(cd "$2" && pwd)"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
RESULTS="$WORK/results"
mkdir -p "$RESULTS"

RESULTS="$RESULTS" bash "$REPO/scripts/offline/install_runtime.sh" "$ASSETS" "$WORK/install"

echo "== flow"
START=$SECONDS
mkdir -p "$WORK/flow-tmp"
env -i HOME="$HOME" PATH=/usr/bin:/bin TMPDIR="$WORK/flow-tmp" LANG=C.UTF-8 \
  OPNCAFYR_RUNTIME_FILE="$WORK/install/runtime/runtime.json" OPNCAFYR_APP_ROOT="$WORK/app" \
  "$WORK/install/envs/launcher/.venv/bin/python" "$REPO/scripts/offline/flow_check.py" "$WORK/flow" \
  --results "$RESULTS"
echo "   flow: $((SECONDS - START)) s"
echo "{\"step\": \"flow\", \"seconds\": $((SECONDS - START))}" >> "$RESULTS/timings.jsonl"

echo "offline phase complete"
