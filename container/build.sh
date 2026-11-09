#!/usr/bin/env bash
# Build the opncafyr image locally (#42).
#
#   container/build.sh [--full] [--platform linux-x86_64|linux-aarch64] [--tag NAME]
#                      [--assets DIR] [--no-check]
#
# Every build argument comes from runtime/pins.toml (scripts/container_args.py). opnca-r is
# private: the token is passed as a BuildKit secret, taken from GH_TOKEN or `gh auth token`,
# and never lands in a layer. With --assets DIR (a folder from scripts/offline/fetch.py,
# fetched and verified elsewhere) nothing is downloaded and no token is needed.
# The default platform is this machine's; another one runs under emulation (slow).
# Afterwards scripts/container/check_image.sh runs unless --no-check.
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
target=slim
tag=""
assets=""
check=1
case "$(uname -m)" in
  arm64 | aarch64) platform=linux-aarch64 ;;
  *) platform=linux-x86_64 ;;
esac
while (($#)); do
  case "$1" in
    --full) target=full ;;
    --platform) platform="$2"; shift ;;
    --tag) tag="$2"; shift ;;
    --assets) assets="$(cd "$2" && pwd)"; shift ;;
    --no-check) check=0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done
case "$platform" in
  linux-x86_64) docker_platform=linux/amd64 ;;
  linux-aarch64) docker_platform=linux/arm64 ;;
  *) echo "unknown platform: $platform" >&2; exit 2 ;;
esac
version="$(cat "$REPO/VERSION")"
suffix=""
if [[ $target == full ]]; then suffix=-full; fi
tag="${tag:-opncafyr:$version-$platform$suffix}"

build_args=()  # no mapfile: macOS ships bash 3.2
while IFS= read -r line; do
  build_args+=("$line")
done < <(cd "$REPO" && uv run --quiet python scripts/container_args.py build-args --platform "$platform")
argv=(docker buildx build "$REPO" --file "$REPO/container/Dockerfile"
  --platform "$docker_platform" --target "$target" --tag "$tag" --load
  --provenance=false --sbom=false
  --build-arg "GIT_SHA=$(git -C "$REPO" rev-parse HEAD)" "${build_args[@]}")
if [[ -n "$assets" ]]; then
  argv+=(--build-context "assets=$assets")
else
  if [[ -z "${GH_TOKEN:-}" ]]; then
    GH_TOKEN="$(gh auth token)"
  fi
  export GH_TOKEN
  argv+=(--secret id=gh_token,env=GH_TOKEN)
fi
"${argv[@]}"
echo "built $tag"
if ((check)); then
  "$REPO/scripts/container/check_image.sh" "$tag" "$target"
fi
