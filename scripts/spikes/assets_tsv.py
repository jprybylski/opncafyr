"""Turn spike_assets.py's JSON into the assets.tsv the shell spikes read (#7).

    uv run python scripts/spikes/spike_assets.py --platform macos-arm64 > a.json
    uv run python scripts/spikes/assets_tsv.py a.json --platform macos-arm64 > DIR/assets.tsv

Columns: kind, name, file, sha256, url. Adds the repair-path files the spikes also try
(the Rust standalone toolchain and opnca-r's vendored source tarball). Rows without a
URL (``missing-wheel``) are kept so the install script can report them.
"""

from __future__ import annotations

import argparse
import json
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("json", type=Path)
    parser.add_argument("--platform", required=True)
    args = parser.parse_args()

    rows = [
        (a["kind"], a["name"], a["local_name"], a["sha256"], a["url"])
        for a in json.loads(args.json.read_text("utf-8"))
    ]
    pins = tomllib.loads((ROOT / "runtime/pins.toml").read_text("utf-8"))
    rust = pins["rust"]["standalone"]["assets"][args.platform]
    rows.append(
        ("rust-standalone", "rust", rust["url"].rsplit("/", 1)[1], rust["sha256"], rust["url"])
    )
    opnca = pins["packages"]["opnca-r"]
    vendored = next(f for f in opnca["assets"] if f.endswith("-vendored.tar.gz"))
    rows.append(
        (
            "package:opnca-r-vendored",
            "opnca-r",
            vendored,
            opnca["assets"][vendored],
            opnca["release_url"] + vendored,
        )
    )
    for row in rows:
        print("\t".join(row))
    return 0


if __name__ == "__main__":
    sys.exit(main())
