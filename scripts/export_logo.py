"""Export the opncafyr logo from its SVG sources (#54).

The SVGs in ``src/opncafyr/web/static/`` are the artwork; everything else is exported from
them by this script and committed. Never edit an export by hand: change the SVG and rerun

    uv run --group integration python scripts/export_logo.py          # write every export
    uv run python scripts/export_logo.py --check                      # exports up to date?

Sources:

- ``logo.svg``: the mark and the wordmark, dark lettering (light backgrounds).
- ``logo-dark.svg``: the same with light lettering (dark backgrounds).
- ``logo-mark.svg``: the mark alone, square, no text. The favicon and app icons use it.

The wordmark is live ``<text>`` set in the system's Avenir Next, so exports that contain
it are made on macOS; the script refuses to write them elsewhere. Pages that can't rely on
that font (the README, the docs site) use the PNG exports, never ``logo.svg`` itself.

Exports (all transparent PNG unless noted):

- ``web/static/logo.png``, ``logo-dark.png`` (1000x320) and ``logo-mark.png`` (512x512).
- ``web/static/favicon-32.png``: the PNG fallback next to the SVG favicon.
- ``installer/assets/opncafyr.icns`` (macOS app icon) and ``opncafyr.ico`` (Windows
  shortcut icon): PNG entries packed by this script, so no iconutil or ImageMagick is
  needed. The installer copies them; nothing is fetched at install or run time.
- ``docs/design/logo-sizes.png``: the mark at 16-256 px and the wordmark, on the app's
  light and dark backgrounds. Look at it after a change: it is the record that the mark
  still reads at the sizes it is used (favicon 16-32, header 32, app icons 16-1024).
- ``docs/design/logo-exports.json``: the SHA-256 of every source and export.
  ``--check`` (and ``tests/test_logo_exports.py``) fail when a source changed without a
  new export, or an export was edited.

Rendering uses Playwright's headless Chromium at device scale 1 (installed with
``uv run --group integration playwright install chromium``). Nothing leaves the machine:
the SVGs are loaded as data URLs into a blank page.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import struct
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
STATIC = REPO / "src" / "opncafyr" / "web" / "static"
ASSETS = REPO / "src" / "opncafyr" / "installer" / "assets"
DESIGN = REPO / "docs" / "design"
MANIFEST = DESIGN / "logo-exports.json"

SOURCES = {
    "logo": STATIC / "logo.svg",
    "logo-dark": STATIC / "logo-dark.svg",
    "mark": STATIC / "logo-mark.svg",
}
# (source, output, width, height)
PNGS = [
    ("logo", STATIC / "logo.png", 1000, 320),
    ("logo-dark", STATIC / "logo-dark.png", 1000, 320),
    ("mark", STATIC / "logo-mark.png", 512, 512),
    ("mark", STATIC / "favicon-32.png", 32, 32),
]
ICO = ASSETS / "opncafyr.ico"
ICNS = ASSETS / "opncafyr.icns"
SHEET = DESIGN / "logo-sizes.png"
ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)
# ICNS element types holding PNG data, by pixel size: the set iconutil writes, with PNG
# icp4/icp5 for 16 and 32. (icp6 is left out: readers disagree on its size.)
ICNS_TYPES = (
    (b"icp4", 16),
    (b"icp5", 32),
    (b"ic11", 32),  # 16@2x
    (b"ic12", 64),  # 32@2x
    (b"ic07", 128),
    (b"ic13", 256),  # 128@2x
    (b"ic08", 256),
    (b"ic14", 512),  # 256@2x
    (b"ic09", 512),
    (b"ic10", 1024),  # 512@2x
)
SHEET_SIZES = (16, 24, 32, 48, 64, 128, 256)
LIGHT_BG, DARK_BG = "#f2f5f4", "#0c1413"  # the app's --bg tokens (app.css)


def rel(path: Path) -> str:
    return path.relative_to(REPO).as_posix()


def sha256(path: Path) -> str:
    data = path.read_bytes()
    if path.suffix == ".svg":  # text: a Windows checkout may have CRLF line endings
        data = data.replace(b"\r\n", b"\n")
    return hashlib.sha256(data).hexdigest()


def outputs() -> list[Path]:
    return [out for _, out, _, _ in PNGS] + [ICO, ICNS, SHEET]


# --- packing (pure Python) ---------------------------------------------------------------


def pack_ico(pngs: dict[int, bytes]) -> bytes:
    """An ICO of PNG images (Windows Vista and later read PNG entries)."""
    sizes = sorted(pngs)
    header = struct.pack("<HHH", 0, 1, len(sizes))
    offset = len(header) + 16 * len(sizes)
    entries, data = b"", b""
    for size in sizes:
        png = pngs[size]
        dim = 0 if size >= 256 else size  # 0 means 256
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(png), offset)
        data += png
        offset += len(png)
    return header + entries + data


def pack_icns(pngs: dict[int, bytes]) -> bytes:
    """An ICNS of PNG elements, the layout ``iconutil`` writes."""
    body = b""
    for kind, size in ICNS_TYPES:
        png = pngs[size]
        body += kind + struct.pack(">I", 8 + len(png)) + png
    return b"icns" + struct.pack(">I", 8 + len(body)) + body


def png_size(data: bytes) -> tuple[int, int]:
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG")
    return struct.unpack(">II", data[16:24])


# --- rendering ---------------------------------------------------------------------------


def data_url(path: Path) -> str:
    return "data:image/svg+xml;base64," + base64.b64encode(path.read_bytes()).decode()


def render(page, source: Path, width: int, height: int) -> bytes:
    page.set_viewport_size({"width": width, "height": height})
    page.set_content(
        "<!doctype html><html><body style='margin:0;background:transparent'>"
        f"<img id='i' src='{data_url(source)}' width='{width}' height='{height}' "
        "style='display:block'></body></html>"
    )
    page.wait_for_function("document.getElementById('i').complete")
    return page.screenshot(
        omit_background=True, clip={"x": 0, "y": 0, "width": width, "height": height}
    )


def contact_sheet(page) -> bytes:
    def row(bg: str, fg: str, wordmark: Path) -> str:
        marks = "".join(
            f"<figure><img src='{data_url(SOURCES['mark'])}' width='{s}' height='{s}'>"
            f"<figcaption>{s}</figcaption></figure>"
            for s in SHEET_SIZES
        )
        return (
            f"<section style='background:{bg};color:{fg}'><div class='marks'>{marks}</div>"
            f"<img src='{data_url(wordmark)}' width='500' height='160'>"
            f"<img src='{data_url(wordmark)}' width='150' height='48'></section>"
        )

    page.set_viewport_size({"width": 1100, "height": 400})
    page.set_content(
        "<!doctype html><html><head><style>"
        "body{margin:0;font:12px system-ui,sans-serif}"
        "section{padding:16px 24px;display:flex;flex-wrap:wrap;align-items:center;gap:24px}"
        ".marks{display:flex;align-items:flex-end;gap:18px}"
        "figure{margin:0;text-align:center}figure img{display:block;margin:0 auto 4px}"
        "</style></head><body>"
        + row(LIGHT_BG, "#475755", SOURCES["logo"])
        + row(DARK_BG, "#a3b3b1", SOURCES["logo-dark"])
        + "</body></html>"
    )
    page.wait_for_function("[...document.images].every(i => i.complete)")
    return page.screenshot(full_page=True)


def export() -> None:
    if sys.platform != "darwin":
        sys.exit("export the logo on macOS: the wordmark is set in Avenir Next (see --help)")
    from playwright.sync_api import sync_playwright

    ASSETS.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(device_scale_factor=1)
        for name, out, width, height in PNGS:
            out.write_bytes(render(page, SOURCES[name], width, height))
        icon_sizes = sorted(set(ICO_SIZES) | {s for _, s in ICNS_TYPES})
        mark = {s: render(page, SOURCES["mark"], s, s) for s in icon_sizes}
        ICO.write_bytes(pack_ico({s: mark[s] for s in ICO_SIZES}))
        ICNS.write_bytes(pack_icns(mark))
        SHEET.write_bytes(contact_sheet(page))
        browser.close()
    manifest = {
        "about": "Written by scripts/export_logo.py; do not edit. SHA-256 of each file.",
        "sources": {rel(p): sha256(p) for p in SOURCES.values()},
        "exports": {rel(p): sha256(p) for p in outputs()},
    }
    MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n", "utf-8")
    for path in outputs():
        print(f"wrote {rel(path)} ({path.stat().st_size} bytes)")


def check() -> list[str]:
    """Problems with the committed exports; empty when they match their sources."""
    try:
        manifest = json.loads(MANIFEST.read_text("utf-8"))
    except (OSError, ValueError):
        return [f"{rel(MANIFEST)} is missing or not JSON; run scripts/export_logo.py"]
    problems = []
    for path in SOURCES.values():
        if manifest["sources"].get(rel(path)) != sha256(path):
            problems.append(f"{rel(path)} changed since the last export; rerun the export")
    for path in outputs():
        if not path.is_file():
            problems.append(f"{rel(path)} is missing")
        elif manifest["exports"].get(rel(path)) != sha256(path):
            problems.append(f"{rel(path)} differs from its export; rerun, don't edit it")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--check", action="store_true", help="only check the exports")
    args = parser.parse_args(argv)
    if args.check:
        problems = check()
        for problem in problems:
            print(problem, file=sys.stderr)
        return 1 if problems else 0
    export()
    return 0


if __name__ == "__main__":
    sys.exit(main())
