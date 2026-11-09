"""Container values from ``runtime/pins.toml`` (#42): the one source for image builds.

    python scripts/container_args.py build-args [--platform linux-x86_64|linux-aarch64]
    python scripts/container_args.py asset <section> <platform>   # "url sha256 file"
    python scripts/container_args.py labels

``build-args`` prints one ``--build-arg=NAME=VALUE`` per line, for ``docker buildx build``
(``container/build.sh`` and ``.github/workflows/container.yml``). Nothing else may set
these values, so the image and the pins can't diverge.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import tomllib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PINS = ROOT / "runtime" / "pins.toml"


def load(path: Path = PINS) -> dict[str, Any]:
    return tomllib.loads(path.read_text("utf-8"))


def section(pins: dict[str, Any], dotted: str) -> dict[str, Any]:
    node: Any = pins
    for part in dotted.split("."):
        node = node[part]
    return node


def asset(pins: dict[str, Any], dotted: str, platform: str) -> tuple[str, str, str]:
    entry = section(pins, dotted)["assets"][platform]
    url = entry["url"]
    return url, entry["sha256"], url.rsplit("/", 1)[1].replace("%2B", "+")


def pins_sha256(path: Path = PINS) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_args(pins: dict[str, Any], platform: str) -> dict[str, str]:
    """Values the Dockerfile needs that aren't files in the fetched assets folder."""
    uv_url, uv_sha, _ = asset(pins, f"ci.{platform}.uv", platform)
    return {
        "BASE_IMAGE": pins["container"]["base_image"],
        "PLATFORM": platform,
        "UV_URL": uv_url,
        "UV_SHA256": uv_sha,
        "OPNCAFYR_VERSION": (ROOT / "VERSION").read_text("utf-8").strip(),
        "PINS_SHA256": pins_sha256(),
        "R_VERSION": pins["r"]["version"],
        "R_DEB_FILE": asset(pins, f"ci.{platform}.r", platform)[2],
        "LIBREOFFICE_FILE": asset(pins, "container.libreoffice", platform)[2],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build-args")
    b.add_argument("--platform", default="linux-x86_64", choices=("linux-x86_64", "linux-aarch64"))
    a = sub.add_parser("asset")
    a.add_argument("section")
    a.add_argument("platform")
    sub.add_parser("labels")
    args = parser.parse_args(argv)
    pins = load()
    if args.cmd == "build-args":
        for name, value in build_args(pins, args.platform).items():
            print(f"--build-arg={name}={value}")
    elif args.cmd == "asset":
        print(*asset(pins, args.section, args.platform))
    else:
        print(f"io.opncafyr.pins.sha256={pins_sha256()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
