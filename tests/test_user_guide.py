"""#40: the user guide is complete, offline, and served inside the app.

The guide's pages are ``docs/user-guide/*.md``. They are shipped in the wheel and shown
by the home page's Guide view, so they may link only to each other and to their images.
"""

from __future__ import annotations

import re
from html import unescape
from pathlib import Path

import pytest

from opncafyr.core.guide import guide_root
from test_guidemd import NODE, parse

ROOT = Path(__file__).resolve().parents[1]
GUIDE = ROOT / "docs" / "user-guide"
INDEX_HTML = ROOT / "src" / "opncafyr" / "web" / "static" / "index.html"
LINK = re.compile(r"(?<!!)\[[^\]]*\]\(([^)\s]+)\)")
IMAGE = re.compile(r"!\[[^\]]*\]\(([^)\s]+)\)")
MAX_IMAGE_BYTES = 400_000

pytestmark = pytest.mark.skipif(not (GUIDE / "index.md").is_file(), reason="no guide yet")


def pages() -> dict[str, str]:
    return {p.stem: p.read_text("utf-8") for p in sorted(GUIDE.glob("*.md"))}


def banner_text() -> str:
    html = INDEX_HTML.read_text("utf-8")
    span = re.search(r'<p class="notice"[^>]*>.*?<span>(.*?)</span>', html, re.S).group(1)
    return " ".join(unescape(re.sub(r"<[^>]+>", "", span)).split())


def test_the_research_notice_matches_the_app_banner():
    notice = banner_text()
    assert notice.startswith("Research use only.")
    first_lines = "\n".join(pages()["index"].splitlines()[:5]).replace("**", "")
    assert notice in first_lines


def test_every_page_is_in_the_contents():
    linked = {m.split("#")[0][:-3] for m in LINK.findall(pages()["index"]) if ".md" in m}
    assert set(pages()) - {"index"} <= linked


def test_links_stay_inside_the_guide_and_resolve():
    all_pages = pages()
    anchors = {}
    if NODE is not None:
        anchors = {
            name: {b["id"] for b in parse(text) if b["type"] == "heading"}
            for name, text in all_pages.items()
        }
    for name, text in all_pages.items():
        for target in LINK.findall(text):
            assert not re.match(r"[a-z]+:", target), f"{name}.md links outside the guide: {target}"
            if re.fullmatch(r"example/[\w.-]+\.csv", target):
                assert (GUIDE / target).is_file(), f"{name}.md links to a missing file: {target}"
                continue
            page, _, anchor = target.partition("#")
            page = page[:-3] if page.endswith(".md") else (name if page == "" else page)
            assert page in all_pages, f"{name}.md links to a missing page: {target}"
            if anchor and anchors:
                assert anchor in anchors[page], f"{name}.md links to a missing heading: {target}"


def test_no_raw_html():
    for name, text in pages().items():
        prose = re.sub(r"```.*?```", "", text, flags=re.S)
        prose = re.sub(r"`[^`]*`", "", prose)
        assert not re.search(r"<[a-zA-Z/][^>]*>", prose), f"raw HTML in {name}.md"


def test_images_exist_are_small_and_used():
    used = {m for text in pages().values() for m in IMAGE.findall(text)}
    for src in used:
        assert re.fullmatch(r"images/[a-z0-9-]+\.png", src), src
        path = GUIDE / src
        assert path.is_file(), f"missing {src}"
        assert path.stat().st_size <= MAX_IMAGE_BYTES, f"{src} is over 400 KB"
    on_disk = {f"images/{p.name}" for p in (GUIDE / "images").glob("*.png")}
    assert on_disk <= used, f"images no page uses: {sorted(on_disk - used)}"


def test_the_example_data_is_the_fixture():
    example = GUIDE / "example" / "two_analyte.csv"
    assert example.read_bytes() == (ROOT / "tests" / "fixtures" / "two_analyte.csv").read_bytes()


def test_the_qc_scripts_are_the_same_in_both_guides():
    """#71: the review and PKNCA scripts were run as printed on the website; the in-app
    copy must not drift from them."""
    fence = re.compile(r"```r\n(.*?)```", re.S)
    in_app = fence.findall((GUIDE / "qc.md").read_text("utf-8"))
    website = fence.findall((ROOT / "site" / "guides" / "qc.md").read_text("utf-8"))
    assert len(website) == 2
    assert in_app == website


def test_no_validation_claims():
    for name, text in pages().items():
        lowered = text.lower()
        for phrase in (
            "is validated",
            "fully validated",
            "gxp compliant",
            "21 cfr part 11 compliant",
        ):
            assert phrase not in lowered, f"{name}.md: {phrase!r}"


def test_guide_root_is_the_guide():
    assert guide_root() is not None
    assert (guide_root() / "index.md").read_bytes() == (GUIDE / "index.md").read_bytes()


def test_the_app_serves_the_guide_to_its_own_page_only(client):
    page = client.get("/static/guide/index.md")
    assert page.status_code == 200 and "Research use only" in page.text
    assert client.get("/static/guide/example/two_analyte.csv").status_code == 200
    assert client.get("/static/guide/../../web/static/index.html").status_code == 404
    assert client.get("/static/guide/%2e%2e/%2e%2e/pyproject.toml").status_code == 404
    client.cookies.clear()
    assert client.get("/static/guide/index.md").status_code in (401, 403)
