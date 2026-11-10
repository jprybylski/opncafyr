#!/bin/bash
# opncafyr per-user installer for macOS and Ubuntu 24.04 Linux (#19, #36, #65). No
# administrator rights, no sudo.
#
#   ./install.sh [install]  [--from-bundle DIR|ZIP] [--assets DIR] [--with-libreoffice] [--without-git] [--dry-run]
#   ./install.sh repair     (same options)
#   ./install.sh uninstall  [--force] [--dry-run]
#   ./install.sh status
#
# This wrapper only finds the pinned python-build-standalone archive (in the download
# cache, the bundle, an --assets folder, or by downloading it from its pinned URL unless
# --from-bundle is given), checks its SHA-256 from bootstrap.tsv, unpacks it under
# <app root>/cache/bootstrap/ and runs run_installer.py with it. Everything else is
# opncafyr.installer (docs/install.md).
#
# Linux: the bundle is a ZIP, and GNU tar can't read one, so this reads it with unzip, bsdtar
# or the system's python3 (zipfile), whichever exists. The Python it unpacks is the pinned
# one; nothing here needs the network when --from-bundle is given.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PATH="/usr/bin:/bin:/usr/sbin:/sbin"

if [ "$(id -u)" -eq 0 ]; then
  echo "error: don't run the installer as root or with sudo; it installs for the current user only." >&2
  exit 1
fi
OS="$(uname -s)"
MACHINE="$(uname -m)"
case "$OS" in
  Darwin)
    ROOT="${OPNCAFYR_APP_ROOT:-$HOME/Library/Application Support/opncafyr}"
    if [ "$MACHINE" = "arm64" ] || [ "$(sysctl -n hw.optional.arm64 2>/dev/null || true)" = "1" ]; then
      PLATFORM=macos-arm64
    else
      PLATFORM=macos-x86_64
    fi
    ;;
  Linux)
    ROOT="${OPNCAFYR_APP_ROOT:-${XDG_DATA_HOME:-$HOME/.local/share}/opncafyr}"
    case "$MACHINE" in
      x86_64|amd64) PLATFORM=linux-x86_64 ;;
      aarch64|arm64) PLATFORM=linux-aarch64 ;;
      *) echo "error: opncafyr supports x86_64 and aarch64 Linux; this is $MACHINE." >&2; exit 1 ;;
    esac
    ;;
  *)
    echo "error: this installer is for macOS and Linux. On Windows run install.bat." >&2
    exit 1
    ;;
esac

LINE="$(awk -F'\t' -v p="$PLATFORM" '$1 == p { print; exit }' "$HERE/bootstrap.tsv")"
if [ -z "$LINE" ]; then
  echo "error: bootstrap.tsv has no Python for $PLATFORM" >&2
  exit 1
fi
FILE="$(printf '%s' "$LINE" | cut -f2)"
SHA="$(printf '%s' "$LINE" | cut -f3)"
URL="$(printf '%s' "$LINE" | cut -f4)"

BUNDLE=""
SOURCES=()
prev=""
for arg in "$@"; do
  case "$prev" in
    --from-bundle) BUNDLE="$arg"; SOURCES+=("$arg") ;;
    --assets) SOURCES+=("$arg") ;;
  esac
  case "$arg" in
    --from-bundle=*) BUNDLE="${arg#*=}"; SOURCES+=("$BUNDLE") ;;
    --assets=*) SOURCES+=("${arg#*=}") ;;
  esac
  prev="$arg"
done

BOOT="$ROOT/cache/bootstrap/$SHA"
ARCHIVE="$BOOT/$FILE"
if command -v sha256sum >/dev/null 2>&1; then
  hash_of() { sha256sum "$1" | awk '{print $1}'; }
elif command -v shasum >/dev/null 2>&1; then
  hash_of() { shasum -a 256 "$1" | awk '{print $1}'; }
else
  echo "error: need sha256sum or shasum to check the pinned download." >&2
  exit 1
fi
verified() { [ -f "$1" ] && [ "$(hash_of "$1")" = "$SHA" ]; }

# Read a ZIP bundle: macOS's tar is bsdtar and reads it; on Linux use unzip, bsdtar or python3.
zip_members() {
  if command -v unzip >/dev/null 2>&1; then unzip -Z1 "$1" 2>/dev/null
  elif command -v bsdtar >/dev/null 2>&1; then bsdtar -tf "$1" 2>/dev/null
  elif [ "$OS" = Darwin ]; then tar -tf "$1" 2>/dev/null
  elif command -v python3 >/dev/null 2>&1; then
    python3 -c 'import sys, zipfile; print("\n".join(zipfile.ZipFile(sys.argv[1]).namelist()))' "$1" 2>/dev/null
  else
    echo "error: can't read the bundle ZIP: install unzip, or run it from an unzipped folder." >&2
    return 1
  fi
}
zip_cat() {
  if command -v unzip >/dev/null 2>&1; then unzip -p "$1" "$2"
  elif command -v bsdtar >/dev/null 2>&1; then bsdtar -xOf "$1" "$2"
  elif [ "$OS" = Darwin ]; then tar -xOf "$1" "$2"
  else
    python3 -c 'import sys, zipfile; sys.stdout.buffer.write(zipfile.ZipFile(sys.argv[1]).read(sys.argv[2]))' "$1" "$2"
  fi
}

if ! verified "$ARCHIVE"; then
  mkdir -p "$BOOT"
  rm -f "$ARCHIVE.part"
  for src in ${SOURCES[@]+"${SOURCES[@]}"}; do
    if [ -d "$src" ]; then
      for candidate in "$src/assets/python/$FILE" "$src/$FILE"; do
        if verified "$candidate"; then cp "$candidate" "$ARCHIVE.part"; break 2; fi
      done
    elif [ -f "$src" ]; then
      member="$(zip_members "$src" | grep -m1 "assets/python/$FILE\$" || true)"
      if [ -n "$member" ]; then
        zip_cat "$src" "$member" > "$ARCHIVE.part"
        if verified "$ARCHIVE.part"; then break; fi
        rm -f "$ARCHIVE.part"
      fi
    fi
  done
  if [ ! -f "$ARCHIVE.part" ]; then
    if [ -n "$BUNDLE" ]; then
      echo "error: python: $FILE is missing from the bundle (or doesn't match its pinned SHA-256)." >&2
      exit 1
    fi
    command -v curl >/dev/null 2>&1 || { echo "error: curl is needed to download the pinned Python; use --from-bundle." >&2; exit 1; }
    echo "downloading the pinned Python ($FILE)"
    curl --silent --show-error --fail --location --proto =https --proto-redir =https \
      --retry 3 --connect-timeout 30 --output "$ARCHIVE.part" "$URL"
  fi
  if ! verified "$ARCHIVE.part"; then
    rm -f "$ARCHIVE.part"
    echo "error: python: $FILE does not match its pinned SHA-256." >&2
    exit 1
  fi
  mv "$ARCHIVE.part" "$ARCHIVE"
fi

PY="$BOOT/python/bin/python3"
if [ ! -x "$PY" ]; then
  rm -rf "$BOOT/python" "$BOOT/python.tmp"
  mkdir -p "$BOOT/python.tmp"
  tar -xzf "$ARCHIVE" -C "$BOOT/python.tmp"
  mv "$BOOT/python.tmp/python" "$BOOT/python"
  rmdir "$BOOT/python.tmp"
  # Verified above; a browser's quarantine flag must not stop it.
  if [ "$OS" = Darwin ]; then xattr -dr com.apple.quarantine "$BOOT" 2>/dev/null || true; fi
fi
if ! "$PY" -I -c 'import sys' 2>/dev/null; then
  echo "error: the bundled Python can't run here. If your organisation blocks programs in" >&2
  echo "  $ROOT, ask IT to allow it (see docs/install.md)." >&2
  exit 1
fi

if [ $# -eq 0 ]; then set -- install; fi
exec "$PY" -I -B "$HERE/run_installer.py" "$@"
