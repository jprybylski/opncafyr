#!/usr/bin/env bash
# Preview or build the documentation website (#53). See README.md in this folder.
#
#   ./preview.sh          sync shared assets, then serve with live reload at
#                         http://127.0.0.1:4000/opncafyr/
#   ./preview.sh build    sync, build into _site/ under the /opncafyr base path, and check
#                         internal links, anchors, images and alt text (html-proofer)
#   ./preview.sh sync     only copy the shared assets (CI runs this before its build)
set -euo pipefail
cd "$(dirname "$0")"
BASE=/opncafyr

# One source for what the site shares with the app: the in-app guide's screenshots and
# practice file, and the logo exports. They are copied at build time, never committed
# here (see .gitignore), so the two guides can't drift apart.
sync() {
  rm -rf assets/screenshots assets/images assets/example
  mkdir -p assets/screenshots assets/images assets/example
  cp ../docs/user-guide/images/*.png assets/screenshots/
  cp ../docs/user-guide/example/two_analyte.csv assets/example/
  for f in logo.png logo-dark.png logo-mark.svg favicon-32.png; do
    cp "../src/opncafyr/web/static/$f" assets/images/
  done
}

bundle_install() {
  bundle config set --local path vendor/bundle >/dev/null
  bundle install --quiet
}

proof() {
  bundle exec htmlproofer _site \
    --disable-external \
    --swap-urls "^${BASE}/:/" \
    --ignore-urls '/^https?:/'
}

case "${1:-serve}" in
  sync) sync ;;
  build)
    sync
    bundle_install
    JEKYLL_ENV=production bundle exec jekyll build --baseurl "$BASE"
    proof
    ;;
  serve)
    sync
    bundle_install
    bundle exec jekyll serve --livereload --baseurl "$BASE"
    ;;
  *) echo "usage: $0 [serve|build|sync]" >&2; exit 2 ;;
esac
