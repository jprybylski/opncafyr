"""Browser smoke check of the built docs website (#53).

Serves a built site (``site/_site``, built with ``--baseurl /opncafyr``) under the same
``/opncafyr/`` path GitHub Pages uses, then opens the key pages in headless Chromium at a
laptop and a phone width, in light and dark::

    site/preview.sh build
    uv run --group ui python scripts/site_smoke.py site/_site

It fails when a page or any of its requests doesn't load (a broken base path shows up
here), an image is broken or has no meaningful alt text, a phone-width page scrolls
sideways, the first Tab doesn't reach the skip link, search finds nothing, or axe reports
a WCAG 2.2 A/AA violation. Nothing is fetched from outside 127.0.0.1: other requests are
blocked and reported.
"""

from __future__ import annotations

import argparse
import functools
import http.server
import re
import sys
import threading
from pathlib import Path

BASE = "/opncafyr/"
ROUTES = (
    "",
    "get-started/install-macos/",
    "get-started/install-windows/",
    "get-started/walkthrough/",
    "get-started/walkthrough-map/",
    "guides/troubleshooting/",
    "guides/qc/",
)
VIEWPORTS = {"desktop": (1280, 800), "mobile": (390, 844)}
SCHEMES = ("light", "dark")
AXE_TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]
# Alt text that names a file or says nothing about the picture.
BAD_ALT = re.compile(r"(\.(png|jpe?g|svg|gif)$)|^(image|screenshot|picture|img|logo)$", re.I)


class _Handler(http.server.SimpleHTTPRequestHandler):
    """Serves the site folder at /opncafyr/ only, like a GitHub Pages project site."""

    def translate_path(self, path: str) -> str:
        clean = path.split("?", 1)[0].split("#", 1)[0]
        if not clean.startswith(BASE):
            return str(Path(self.directory) / "__outside_base_path__")
        return super().translate_path("/" + clean[len(BASE) :])

    def log_message(self, format: str, *args) -> None:  # noqa: A002 - stdlib signature
        pass


def serve(root: Path) -> tuple[http.server.ThreadingHTTPServer, str]:
    handler = functools.partial(_Handler, directory=str(root))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def check_page(page, url: str, label: str, run_axe: bool) -> list[str]:
    problems: list[str] = []
    bad: list[str] = []
    page.on(
        "response",
        lambda r: bad.append(f"{r.status} {r.url}") if r.status >= 400 else None,
    )
    page.on("requestfailed", lambda r: bad.append(f"failed {r.url}"))
    page.on("console", lambda m: bad.append(f"console {m.text}") if m.type == "error" else None)

    response = page.goto(url, wait_until="load")
    if response is None or response.status != 200:
        return [f"{label}: {url} answered {response.status if response else 'nothing'}"]
    # Load lazy images too, then look at every image.
    page.evaluate("document.querySelectorAll('img[loading=lazy]').forEach(i => i.loading='eager')")
    page.wait_for_function("[...document.images].every(i => i.complete)", timeout=15000)
    for img in page.evaluate(
        """[...document.images].map(i => ({
            src: i.getAttribute('src'), alt: i.getAttribute('alt'), ok: i.naturalWidth > 0,
            hidden: i.closest('[aria-hidden=true]') !== null,
            visible: i.getClientRects().length > 0}))"""
    ):
        if img["visible"] and not img["ok"]:
            problems.append(f"{label}: broken image {img['src']}")
        if img["alt"] is None:
            problems.append(f"{label}: image without alt {img['src']}")
        elif not img["hidden"] and (len(img["alt"].strip()) < 8 or BAD_ALT.search(img["alt"])):
            problems.append(f"{label}: alt text {img['alt']!r} doesn't describe {img['src']}")
    overflow = page.evaluate(
        "document.documentElement.scrollWidth - document.documentElement.clientWidth"
    )
    if overflow > 1:
        problems.append(f"{label}: the page scrolls sideways by {overflow}px")
    page.keyboard.press("Tab")
    focused = page.evaluate("document.activeElement && document.activeElement.textContent")
    if not focused or "skip" not in focused.lower():
        problems.append(f"{label}: the first Tab focuses {focused!r}, not the skip link")
    if run_axe:
        from axe_playwright_python.sync_playwright import Axe

        results = Axe().run(page, options={"runOnly": {"type": "tag", "values": AXE_TAGS}})
        if results.violations_count:
            problems.append(f"{label}: axe found violations\n{results.generate_report()}")
    problems += [f"{label}: {b}" for b in bad]
    return problems


def check_search(page, origin: str) -> list[str]:
    page.goto(origin + BASE, wait_until="load")
    box = page.locator("#search-input")
    box.click()
    box.press_sequentially("BLQ", delay=50)
    try:
        page.locator(".search-result").first.wait_for(timeout=10000)
    except Exception:  # noqa: BLE001 - report, don't crash
        return ["search: typing 'BLQ' found nothing (is the search index under the base path?)"]
    return []


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("site", type=Path, help="the built site folder (site/_site)")
    args = parser.parse_args(argv)
    if not (args.site / "index.html").is_file():
        parser.error(f"{args.site} has no index.html; build the site first")

    from playwright.sync_api import sync_playwright

    server, origin = serve(args.site.resolve())
    problems: list[str] = []

    def block(route) -> None:
        problems.append(f"outside request {route.request.url}")
        route.abort()

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            for scheme in SCHEMES:
                for name, (width, height) in VIEWPORTS.items():
                    context = browser.new_context(
                        viewport={"width": width, "height": height}, color_scheme=scheme
                    )
                    # Only the local server: anything else would mean the site reaches out.
                    context.route(re.compile(r"^(?!http://127\.0\.0\.1:).*"), block)
                    for route in ROUTES:
                        page = context.new_page()
                        label = f"{scheme}/{name} {BASE}{route}"
                        problems += check_page(page, origin + BASE + route, label, True)
                        page.close()
                    if name == "desktop" and scheme == "light":
                        page = context.new_page()
                        problems += check_search(page, origin)
                        page.close()
                    context.close()
            browser.close()
    finally:
        server.shutdown()
    for problem in problems:
        print(problem, file=sys.stderr)
    checked = len(ROUTES) * len(VIEWPORTS) * len(SCHEMES)
    print(f"{checked} page views checked, {len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
