#!/usr/bin/env bash
# CI (#65): the native Linux install end to end, as an unprivileged user in a minimal Ubuntu 24.04
# container with no network, no compiler, no make, no sudo and no apt at install time.
#
#   scripts/installer_ci/linux.sh <arch: x86_64|aarch64> <payload dir> <results dir> <newer payload dir>
#
# Host side (this script; online): build the Linux bundle twice from independent downloads and
# compare the manifests, build the newer bundle for the update leg, and build the minimal image:
# Ubuntu 24.04 plus exactly the Ubuntu packages the installer's preflight list names (generated
# from src/opncafyr/installer/linux_libs.py, so the two can't drift) and strace. Then run
# linux_inner.sh in it with `--network none`. The preflight's negative leg uses a second image
# with one package removed. check_trace.py then fails on any connection attempt in the trace.
#
# opnca-r is private: the bundle build needs GH_TOKEN (its Linux binary, opnca-r 0.3.3), and
# the flow leg runs two real opnca-r analyses, replays them and publishes a DOCX and a PPTX.
#   env: GH_TOKEN  read access to the private opnca-r release
set -euo pipefail

ARCH="$1"
PAYLOAD="$(cd "$2" && pwd)"
RESULTS="$3"
NEWER="$(cd "$4" && pwd)"
mkdir -p "$RESULTS"
RESULTS="$(cd "$RESULTS" && pwd)"
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
PLATFORM="linux-$ARCH"
case "$ARCH" in
  x86_64) DOCKER_PLATFORM=linux/amd64 ;;
  aarch64) DOCKER_PLATFORM=linux/arm64 ;;
  *) echo "::error::unknown architecture $ARCH"; exit 1 ;;
esac
WORK="$RESULTS/work"
rm -rf "$WORK"
mkdir -p "$WORK"
VERSION="$(cat "$REPO/VERSION")"
fail() { echo "::error::$*"; exit 1; }

[ -n "${GH_TOKEN:-}" ] || { echo "::error::GH_TOKEN (read access to the opnca-r release) is required"; exit 1; }

# The Gate 2 flow driver and the fixture helpers it imports, in the repository layout.
mkdir -p "$WORK/flow/scripts/offline" "$WORK/flow/tests/fixtures/opnca-r"
cp "$REPO/scripts/offline/flow_check.py" "$WORK/flow/scripts/offline/"
cp "$REPO"/tests/{nca_helpers,publish_helpers,artifact_docs,opnca_listing}.py "$WORK/flow/tests/"
cp "$REPO"/tests/fixtures/two_analyte.{csv,expected.json} "$WORK/flow/tests/fixtures/"
cp "$REPO"/tests/fixtures/opnca-r/* "$WORK/flow/tests/fixtures/opnca-r/"
chmod -R a+rX "$WORK/flow"

# ---- bundles (online) ------------------------------------------------------------------
build_bundle() { # <payload dir> <out dir> <app root for the download cache>
  OPNCAFYR_APP_ROOT="$3" bash "$1/install.sh" bundle --platform "$PLATFORM" --out "$2"
}
build_bundle "$PAYLOAD" "$WORK/bundle-a" "$WORK/cache-a" >"$RESULTS/bundle-a.log" 2>&1 || { tail -30 "$RESULTS/bundle-a.log"; fail "bundle build failed"; }
build_bundle "$PAYLOAD" "$WORK/bundle-b" "$WORK/cache-b" >"$RESULTS/bundle-b.log" 2>&1 || { tail -30 "$RESULTS/bundle-b.log"; fail "second bundle build failed"; }
A="$(ls -d "$WORK"/bundle-a/opncafyr-bundle-*)"
B="$(ls -d "$WORK"/bundle-b/opncafyr-bundle-*)"
ID_A="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["bundle_id"])' "$A/bundle.json")"
ID_B="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["bundle_id"])' "$B/bundle.json")"
[ "$ID_A" = "$ID_B" ] || fail "two builds of the same bundle differ: $ID_A vs $ID_B"
echo "bundle $ID_A built twice from independent downloads: identical"
cmp "$A/SHA256SUMS" "$B/SHA256SUMS" || fail "SHA256SUMS differ between the two builds"
# The newer payload reuses the first build's downloads, so this is quick.
OPNCAFYR_APP_ROOT="$WORK/cache-a" bash "$NEWER/install.sh" bundle --platform "$PLATFORM" \
  --out "$WORK/bundle-newer" >"$RESULTS/bundle-newer.log" 2>&1 || { tail -30 "$RESULTS/bundle-newer.log"; fail "newer bundle build failed"; }
NEW="$(ls -d "$WORK"/bundle-newer/opncafyr-bundle-*)"
rm -rf "$WORK/bundle-b" "$WORK/cache-b"

# ---- the minimal image -----------------------------------------------------------------
# linux_libs.py names each library's Ubuntu package; read its table directly (no import needed).
APT="$(python3 - "$REPO/src/opncafyr/installer/linux_libs.py" <<'PY'
import re, sys
print(" ".join(sorted(set(re.findall(r'Lib\("[^"]+", "([^"]+)"\)', open(sys.argv[1]).read())))))
PY
)"
echo "preflight packages: $APT"
cat >"$WORK/Dockerfile" <<DOCKER
FROM ubuntu:24.04
ARG PACKAGES
ARG EXTRA=""
RUN apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends \
      \$PACKAGES fonts-dejavu-core unzip strace ca-certificates >/dev/null \
    && if [ -n "\$EXTRA" ]; then DEBIAN_FRONTEND=noninteractive apt-get remove -y -qq \$EXTRA >/dev/null; fi \
    && rm -rf /var/lib/apt/lists/* \
    && useradd -m -s /bin/bash u \
    && for c in gcc cc g++ c++ make rustc cargo gfortran sudo; do ! command -v \$c >/dev/null || { echo "\$c is present"; exit 1; }; done
DOCKER
docker build -q --platform "$DOCKER_PLATFORM" --build-arg "PACKAGES=$APT" -t opncafyr-linux-ci "$WORK" >/dev/null
# One package fewer: the preflight must refuse this one with a plain message.
docker build -q --platform "$DOCKER_PLATFORM" --build-arg "PACKAGES=$APT" --build-arg "EXTRA=libwebpmux3" \
  -t opncafyr-linux-ci-missing "$WORK" >/dev/null

# ---- the legs, with the network cut ------------------------------------------------------
mkdir -p "$RESULTS/inner"
chmod 777 "$RESULTS/inner"
docker volume rm -f opncafyr-linux-ci-home >/dev/null 2>&1 || true
docker volume create opncafyr-linux-ci-home >/dev/null
docker run --rm --platform "$DOCKER_PLATFORM" -v opncafyr-linux-ci-home:/home/u opncafyr-linux-ci chown u:u /home/u

echo "--- preflight refuses a computer with a library missing"
if docker run --rm --platform "$DOCKER_PLATFORM" --network none --user u -e HOME=/home/u \
     -v "$A:/b:ro" opncafyr-linux-ci-missing /b/installer/install.sh install --from-bundle /b \
     >"$RESULTS/inner/preflight-negative.log" 2>&1; then
  fail "the installer ran on a computer without libwebpmux.so.3"
fi
grep -q "libwebpmux.so.3" "$RESULTS/inner/preflight-negative.log" || { cat "$RESULTS/inner/preflight-negative.log"; fail "the preflight did not name libwebpmux.so.3"; }
grep -q "libwebpmux3" "$RESULTS/inner/preflight-negative.log" || fail "the preflight did not name the package to ask for"
grep -q "administrator" "$RESULTS/inner/preflight-negative.log" || fail "the preflight did not say who can fix it"
echo "refused, naming libwebpmux.so.3 and libwebpmux3"

echo "--- install, repair, launch, tamper, update (no network)"
docker run --rm --platform "$DOCKER_PLATFORM" --network none --user u -e HOME=/home/u \
  --cap-add SYS_PTRACE \
  -v opncafyr-linux-ci-home:/home/u -v "$A:/b:ro" -v "$NEW:/newer:ro" \
  -v "$RESULTS/inner:/results" -v "$WORK/flow:/flowrepo:ro" \
  -v "$REPO/scripts/installer_ci/linux_inner.sh:/inner.sh:ro" \
  opncafyr-linux-ci bash /inner.sh "$VERSION" | tee "$RESULTS/inner/inner.log"

echo "--- no connection attempt left the machine"
python3 "$REPO/scripts/offline/check_trace.py" "$RESULTS/inner"/trace-*.txt \
  --allow "$REPO/scripts/offline/allowed_attempts.toml" --json "$RESULTS/inner/trace-report.json"
