#!/usr/bin/env bash
# opncafyr per-user installer for macOS (stub).
#
# Runs without sudo. Today it only performs preflight detection and exits.
# The full flow (Gate 1/4): download pinned assets from runtime/pins.toml, verify
# SHA-256, install into a staging dir, restore locks, smoke-test, create the .app
# wrapper, then atomically activate. Modes: install | repair | uninstall.
set -euo pipefail

APP_ROOT="${HOME}/Library/Application Support/opncafyr"
MODE="${1:-install}"

if [[ "$(id -u)" -eq 0 ]]; then
  echo "Do not run this installer as root; it installs for the current user only." >&2
  exit 1
fi

os="$(uname -s)"; arch="$(uname -m)"
echo "opncafyr installer (${MODE}) — preflight"
echo "  os:        ${os}"
echo "  arch:      ${arch}"
echo "  app root:  ${APP_ROOT}"

if [[ "${os}" != "Darwin" ]]; then
  echo "  This script supports macOS. Use install.bat on Windows." >&2
  exit 1
fi

parent="$(dirname "${APP_ROOT}")"
if [[ -w "${parent}" ]]; then echo "  writable:  yes"; else echo "  writable:  NO (${parent})"; fi
free_kb="$(df -Pk "${parent}" | awk 'NR==2 {print $4}')"
echo "  free disk: $((free_kb / 1024)) MiB"
if command -v curl >/dev/null 2>&1; then echo "  curl:      yes"; else echo "  curl:      no"; fi

echo
echo "Installation is not implemented yet (see issue #19, Gate 1)." >&2
exit 2
