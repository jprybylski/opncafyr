#!/bin/bash
# Install one stage's Ubuntu packages at their locked versions (#42).
#
#   install.sh <lock file>
#
# The lock lists every package the stage adds or upgrades, at an exact version
# (scripts/container/lock_apt.sh). Anything apt would add or change beyond it fails the
# build, so a moved archive shows up as an error, never as a silent change.
set -euo pipefail
lock="$1"
mapfile -t pkgs < <(grep -v '^#' "$lock" | sed '/^$/d')
state() { dpkg-query -W -f '${Package}=${Version}\n' | sort; }
tmp="$(mktemp -d)"
state > "$tmp/before"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
if ((${#pkgs[@]})); then
  apt-get install -y -qq --no-install-recommends "${pkgs[@]}"
fi
state > "$tmp/after"
comm -13 "$tmp/before" "$tmp/after" > "$tmp/added"
printf '%s\n' "${pkgs[@]}" | sed '/^$/d; s/:[a-z0-9]*=/=/' | sort > "$tmp/expected"
if ! cmp -s "$tmp/expected" "$tmp/added"; then
  echo "apt installed something other than $lock (< locked, > installed):" >&2
  diff "$tmp/expected" "$tmp/added" >&2 || true
  exit 1
fi
rm -rf "$tmp"
apt-get clean
rm -rf /var/lib/apt/lists/*
