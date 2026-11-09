#!/usr/bin/env bash
# Check a built opncafyr image (#42). Run by container/build.sh and the container workflow.
#
#   scripts/container/check_image.sh <image> [slim|full] [--no-smoke]
#
# 1. libraries: every shared object in R, the R library and the Python envs resolves
# 2. no toolchain: no C/C++/Fortran compiler, cargo or rustc anywhere in the image
# 3. fonts: no proprietary fonts (the image must stay redistributable, #43)
# 4. secrets: GH_TOKEN, when set, appears in no file, label, env or history entry
# 5. smoke: the full Gate 2/3 flow (scripts/offline/flow_check.py) with --network none, as
#    a non-root user with no passwd entry; then the study is moved to another path and
#    owner, its runs still verify, and no file in it names a container path
# 6. size: the image's size, uncompressed and gzip-compressed (input for #43's 2 GiB limit)
#
# Prints counts, IDs and hashes only, never data values.
set -euo pipefail
image="${1:?usage: $0 <image> [slim|full] [--no-smoke]}"
variant="${2:-slim}"
smoke=1
[[ "${3:-}" == --no-smoke ]] && smoke=0
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
fail() { echo "FAIL: $*" >&2; exit 1; }
inside() { docker run --rm --network none --entrypoint bash "$image" -euo pipefail -c "$1"; }

echo "== libraries"
missing="$(inside '
  rhome=$(ls -d /opt/R/*/lib/R)
  export LD_LIBRARY_PATH="$rhome/lib"
  # Wheels vendor libraries in <pkg>.libs/ (auditwheel); they resolve each other through
  # the extension modules that load them, which are checked, so ldd on them alone is moot.
  find "$rhome" /opt/opncafyr/rlib /opt/opncafyr/envs /opt/opncafyr/rt/python \
       -type f \( -name "*.so" -o -name "*.so.*" \) -not -path "*.libs/*" -print0 \
    | xargs -0 -n 50 ldd 2>/dev/null | grep "not found" | sort -u || true
  ldd "$rhome/bin/exec/R" | grep "not found" || true')"
[[ -z "$missing" ]] || fail "unresolved shared libraries:"$'\n'"$missing"
if [[ "$variant" == full ]]; then
  # LibreOffice's UI plugins (Qt, KDE, GTK), GStreamer and Java are optional; headless
  # conversion loads none of them.
  missing="$(inside '
    cd /opt/libreoffice*/program
    for f in soffice.bin $(ls ./*.so | grep -vE "vclplug|kf5|qt5|qt6|gtk|gst|jawt|avmedia|java|kde"); do
      LD_LIBRARY_PATH=$PWD ldd "$f" 2>/dev/null | grep "not found" | grep -v libjawt || true
    done | sort -u')"
  [[ -z "$missing" ]] || fail "unresolved LibreOffice libraries:"$'\n'"$missing"
fi
echo "   all shared objects resolve"

echo "== no toolchain"
found="$(inside '
  for c in cc c++ gcc g++ gfortran cpp clang cargo rustc rustup; do
    command -v "$c" || true
  done
  # Executables only (gcc-14-base, the libgcc runtime package, leaves dpkg metadata).
  find / -xdev -type f -perm /111 \( -name "gcc-[0-9]*" -o -name "g++-[0-9]*" \
    -o -name "gfortran*" -o -name "cc1*" -o -name f951 -o -name cargo -o -name rustc \) \
    2>/dev/null || true')"
[[ -z "$found" ]] || fail "toolchain present:"$'\n'"$found"
echo "   none"

echo "== fonts"
bad="$(inside 'dpkg-query -W -f "\${Package}\n" | grep -E "mscorefonts|ttf-ms" || true
  fc-list : family file | grep -Ei "calibri|cambria|(^|[ :/])arial|times new roman|verdana|tahoma" || true')"
[[ -z "$bad" ]] || fail "proprietary fonts:"$'\n'"$bad"
echo "   open fonts only: $(inside 'fc-list : family | sort -u | paste -sd, -')"

echo "== secrets"
if [[ -n "${GH_TOKEN:-}" ]]; then
  # The token goes in on stdin, never as an argument.
  if printf '%s\n' "$GH_TOKEN" | docker run --rm -i --network none --user 0 --entrypoint bash "$image" \
      -c 'grep -rlF -f /dev/stdin / --exclude-dir=proc --exclude-dir=sys --exclude-dir=dev 2>/dev/null' \
      | grep -q .; then
    fail "the token is in a file in the image"
  fi
  if { docker history --no-trunc "$image"; docker image inspect "$image"; } | grep -qF "$GH_TOKEN"; then
    fail "the token is in the image history, labels or environment"
  fi
  echo "   token in no file, layer history, label or env"
else
  echo "   skipped (GH_TOKEN not set)"
fi

if ((smoke)); then
  echo "== smoke (network none, uid 4321)"
  out="$(mktemp -d)"
  chmod 0777 "$out"
  docker run --rm --network none --user 4321:4321 \
    -v "$REPO:/src:ro" -v "$out:/out" \
    -e OPNCAFYR_APP_ROOT=/out/app -e TMPDIR=/out/tmp \
    --entrypoint bash "$image" -euo pipefail -c '
      mkdir -p /out/tmp
      python /src/scripts/offline/flow_check.py /out/work --results /out/results'
  python3 - "$out/results/flow.json" "$variant" <<'PY'
import json, sys
flow = json.load(open(sys.argv[1]))
print("   flow:", json.dumps({k: v for k, v in flow.items() if not isinstance(v, (dict, list))}))
if "error" in flow:
    sys.exit(f"FAIL: the flow stopped: {flow['error']}")
if flow.get("opnca") is not True or "publish_from" in flow:
    sys.exit("FAIL: the flow ran without opnca-r")
for part in ("runs", "publication", "report", "deck"):
    if part not in flow:
        sys.exit(f"FAIL: the flow has no {part}")
PY
  if [[ "$variant" == full ]]; then
    echo "== LibreOffice preview (full)"
    deck="$(cd "$out" && find work -name '*.pptx' -path '*/deck/builds/*' | head -1)"
    [[ -n "$deck" ]] || fail "the smoke flow built no deck"
    docker run --rm --network none --user 4321:4321 -v "$out:/out" -e HOME=/out/tmp \
      --entrypoint bash "$image" -euo pipefail -c '
        soffice="$(/opt/opncafyr/rt/python/bin/python3 -c "import json; print(json.load(open(\"/opt/opncafyr/runtime/runtime.json\"))[\"executables\"][\"soffice\"])")"
        # Its own profile on the container filesystem, as deckifyr gives it.
        "$soffice" -env:UserInstallation=file:///tmp/lo-profile --headless --convert-to pdf --outdir /out/preview "/out/'"$deck"'" > /dev/null'
    [[ -s "$out/preview/$(basename "${deck%.pptx}").pdf" ]] || fail "LibreOffice made no preview"
    echo "   the deck converts to PDF offline"
  fi
  echo "== portable study (other path, other uid)"
  docker run --rm --network none --user 0 -v "$out:/out" --entrypoint bash "$image" -c \
    'cp -a /out/work /moved && chown -R 2468:2468 /moved && mv /moved /out/moved'
  study="$(docker run --rm --network none --user 2468:2468 -v "$out/moved:/elsewhere/study" \
    --entrypoint bash "$image" -c 'cd /elsewhere/study && find . -maxdepth 2 -name .opncafyr -printf "%h\n" | head -1')"
  [[ -n "$study" ]] || fail "no study folder in the flow's work dir"
  docker run --rm --network none --user 2468:2468 -v "$out/moved:/elsewhere/study" \
    -e OPNCAFYR_APP_ROOT=/tmp/app "$image" runs verify "/elsewhere/study/$study"
  # Every manifest and script opncafyr writes must use study-relative paths. Known upstream
  # exception: deckifyr's own deck-manifest.json records absolute paths (deckifyr#97).
  absolute="$(grep -rlF -e "/out/work" -e "/workspace/" "$out/moved/$study" \
    --include='*.json' --include='*.R' --include='*.yaml' --exclude=deck-manifest.json || true)"
  [[ -z "$absolute" ]] || fail "files in the study name a container path:"$'\n'"$absolute"
  echo "   runs verify at another path and owner; no container paths in manifests or scripts"
  docker run --rm --user 0 -v "$out:/out" --entrypoint rm "$image" -rf /out/work /out/moved /out/tmp /out/app /out/preview
fi

echo "== size"
bytes="$(docker image inspect --format '{{.Size}}' "$image")"
gz="$(docker save "$image" | gzip -1 | wc -c | tr -d ' ')"
echo "   $variant: $((bytes / 1048576)) MiB uncompressed, $((gz / 1048576)) MiB gzip"
echo "   image ID: $(docker image inspect --format '{{.Id}}' "$image")"
echo "check passed: $image"
