"""Download the pinned datum for this machine and print its path (CI and dev, #83).

    uv run python scripts/fetch_datum.py --out <dir>

Reads ``[optional.datum]`` from ``runtime/pins.toml``, downloads the asset for this
platform, refuses it unless its SHA-256 matches the pin, unpacks it under ``<dir>`` and
prints the binary's path. The real-binary tests (``tests/test_datum_real.py``) take it
through ``OPNCAFYR_TEST_DATUM``. Not shipped.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "offline"))

from fetch import download  # noqa: E402

from opncafyr.installer import platforms  # noqa: E402
from opncafyr.installer.archives import extract  # noqa: E402
from opncafyr.installer.pins import DATUM, Pins  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--platform", default=None)
    args = parser.parse_args(argv)
    platform = args.platform or platforms.detect()
    pins = Pins.load(ROOT / "runtime")
    (asset,) = [a for a in pins.assets(platform, allow_pending=True) if a.component == DATUM]
    out = args.out.resolve()
    archive = download(asset.url, out / asset.file, asset.sha256)
    extract(archive, out / "datum")
    exe = out / "datum" / ("datum.exe" if platform.startswith("windows") else "datum")
    if not exe.is_file():
        print(f"{asset.file} has no {exe.name}", file=sys.stderr)
        return 1
    print(exe)
    return 0


if __name__ == "__main__":
    sys.exit(main())
