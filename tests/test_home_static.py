"""Rules for the home page's static files (ADR 0006, #49). No browser needed."""

import re
from importlib import resources
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from opncafyr.core.app import HOME_CSP, create_app
from opncafyr.core.lifecycle import Settings
from opncafyr.core.registry import load_descriptors
from opncafyr.core.security import SESSION_HEADER

STATIC = Path(str(resources.files("opncafyr.web").joinpath("static")))
TEXT_SUFFIXES = {".html", ".css", ".js"}


def _static_files() -> list[Path]:
    files = [p for p in STATIC.rglob("*") if p.is_file() and p.suffix in TEXT_SUFFIXES]
    assert files, "no static files found"
    return files


def _is_relative(url: str) -> bool:
    if url.startswith("#"):
        return True
    return not (url.startswith(("/", "\\")) or re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", url))


@pytest.mark.parametrize("tool_id", sorted(load_descriptors()))
def test_no_tool_id_in_core_static_files(tool_id):
    pattern = re.compile(rf"\b{re.escape(tool_id)}\b", re.IGNORECASE)
    for path in _static_files():
        text = path.read_text("utf-8")
        assert not pattern.search(text), f"{path.relative_to(STATIC)} names the tool {tool_id!r}"


def test_tool_id_check_would_catch_a_name():
    # Guards the regex: "opncafyr" must not count as a tool id, a bare id must.
    ids = sorted(load_descriptors())
    assert any(re.search(rf"\b{re.escape(i)}\b", f"open {i} now", re.IGNORECASE) for i in ids)
    assert not any(re.search(rf"\b{re.escape(i)}\b", "opncafyr", re.IGNORECASE) for i in ids)


def _html_files() -> list[Path]:
    pages = sorted(STATIC.rglob("*.html"))
    assert {p.name for p in pages} >= {"index.html", "components.html"}
    return pages


def test_html_asset_urls_are_relative():
    for page in _html_files():
        html = page.read_text("utf-8")
        urls = re.findall(r'\b(?:src|href|action)\s*=\s*"([^"]*)"', html)
        assert urls
        for url in urls:
            assert _is_relative(url), f"{page.name}: {url!r} is not relative"


def test_css_urls_and_imports_are_relative():
    for path in STATIC.rglob("*.css"):
        text = path.read_text("utf-8")
        for url in re.findall(r"url\(\s*['\"]?([^'\")]+)", text):
            assert _is_relative(url), f"{path.name}: url({url})"
        assert "@import" not in text, f"{path.name} imports another stylesheet"


def test_js_imports_and_fetches_are_relative():
    for path in STATIC.rglob("*.js"):
        text = path.read_text("utf-8")
        specs = re.findall(r"""\bimport\b[^'"]*?\bfrom\s*['"]([^'"]+)['"]""", text)
        specs += re.findall(r"""\bimport\s*\(\s*['"]([^'"]+)['"]""", text)
        for spec in specs:
            assert spec.startswith(("./", "../")), f"{path.name}: import {spec!r}"
        for url in re.findall(r"""['"`]((?:https?:)?//[^'"`]*)""", text):
            pytest.fail(f"{path.name}: absolute URL {url!r}")
        for url in re.findall(
            r"""\b(?:getJSON|post|send|fetch|postAndForget)\(\s*(?:"[A-Z]+",\s*)?['"`]([^'"`]*)""",
            text,
        ):
            assert _is_relative(url), f"{path.name}: request to {url!r}"


@pytest.mark.parametrize("name", ["index.html", "components.html"])
def test_no_inline_script_or_style(name):
    html = (STATIC / name).read_text("utf-8")
    assert "<style" not in html
    assert " style=" not in html
    assert not re.search(r"\son[a-z]+\s*=", html), "inline event handler"
    for attrs, body in re.findall(r"<script([^>]*)>(.*?)</script>", html, re.DOTALL):
        assert "src=" in attrs and not body.strip(), "inline script"


def test_shell_structure():
    html = (STATIC / "index.html").read_text("utf-8")
    assert 'class="skip-link" href="#main"' in html
    assert '<main id="main"' in html
    assert "<header" in html and '<nav class="sidenav" aria-label="Main">' in html
    assert "Research use only." in html
    assert re.search(r'id="jobs-live"[^>]*aria-live="polite"', html)
    assert html.count('<script type="module"') == 1
    # The notice and the page-wide status lines sit in the header, outside every view.
    header = html[html.index("<header") : html.index("</header>")]
    assert "Research use only." in header and 'id="jobs-live"' in header
    assert '<html lang="en" data-theme="{{OPNCAFYR_THEME}}">' in html
    for view in ("home", "jobs", "settings"):
        assert f'id="view-{view}"' in html
        assert f'href="#/{"" if view == "home" else view}"' in html
    # The quit states (#73) are in the page from the start, so showing them needs no
    # request after the launcher has gone; the final page starts hidden.
    assert re.search(r'<section id="quit-pending"[^>]*hidden', html)
    assert re.search(r'<main id="end-view"[^>]*hidden', html)
    assert re.search(r'<h1 id="end-title" tabindex="-1"', html)


@pytest.mark.parametrize("theme", [None, "system", "light", "dark"])
def test_home_renders_the_saved_theme(security, tmp_path, theme):
    # Its own settings file: the shared client fixture would use the real state dir.
    app = create_app(
        security, request_shutdown=lambda: None, settings=Settings(tmp_path / "settings.json")
    )
    with TestClient(
        app,
        base_url=f"http://127.0.0.1:{security.port}",
        cookies={security.cookie_name: security.secret},
    ) as c:
        if theme is not None:
            resp = c.put(
                "/api/settings", json={"theme": theme}, headers={SESSION_HEADER: security.secret}
            )
            assert resp.status_code == 200
        html = c.get("/").text
    assert f'<html lang="en" data-theme="{theme or "system"}">' in html
    assert "{{" not in html


def test_home_headers(client):
    resp = client.get("/")
    assert resp.headers["cache-control"] == "no-store"
    assert resp.headers["content-security-policy"] == HOME_CSP
    assert "script-src 'self'" in HOME_CSP and "unsafe" not in HOME_CSP


@pytest.mark.parametrize(
    ("path", "kind"),
    [("static/app.css", "text/css"), ("static/js/main.js", "javascript")],
)
def test_assets_are_served(client, path, kind):
    resp = client.get(f"/{path}")
    assert resp.status_code == 200
    assert kind in resp.headers["content-type"]
    assert resp.headers["cache-control"] == "no-cache"


def test_assets_need_the_loopback_host(client):
    resp = client.get("/static/app.css", headers={"host": "evil.example:54321"})
    assert resp.status_code in (400, 403)


def test_tools_have_display_names(client):
    tools = client.get("/api/tools").json()["tools"]
    assert all(t["display_name"] for t in tools)
