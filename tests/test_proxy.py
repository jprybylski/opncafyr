"""#15 acceptance: the /apps/<tool>/ proxy against a toy child app."""

import pytest
from fastapi.testclient import TestClient

from opncafyr.core.app import create_app
from opncafyr.core.bootstrap import mint_open_token
from opncafyr.core.security import SESSION_HEADER, SecurityConfig
from opncafyr.core.supervisor import ChildState
from toy_adapter import TOKEN_HEADER, toy_supervisor

PORT = 54322
ORIGIN = f"http://127.0.0.1:{PORT}"


@pytest.fixture
def env(tmp_path):
    security = SecurityConfig(port=PORT)
    sup, _ = toy_supervisor(tmp_path)
    app = create_app(security, supervisor=sup, open_tokens=tmp_path / "tokens")
    with TestClient(app, base_url=ORIGIN, follow_redirects=False) as client:
        client.tokens = tmp_path / "tokens"
        client.cookie = {"cookie": f"{security.cookie_name}={security.secret}"}
        client.sup = sup
        client.security = security
        yield client
    assert sup.state("toy") in (ChildState.STOPPED, ChildState.NOT_STARTED)


@pytest.fixture
def running(env):
    env.child = env.sup.start("toy", {})
    return env


def test_open_sets_the_session_cookie_for_the_whole_origin(env):
    resp = env.get(f"/open?token={mint_open_token(env.tokens)}")
    assert resp.status_code == 303 and resp.headers["location"] == "/"
    cookie = resp.headers["set-cookie"]
    assert cookie.startswith(f"{env.security.cookie_name}={env.security.secret};")
    attrs = {a.strip().lower() for a in cookie.split(";")[1:]}
    assert {"httponly", "path=/", "samesite=strict"} <= attrs
    assert resp.headers["cache-control"] == "no-store"
    home = env.get("/", headers=env.cookie)
    assert home.status_code == 200 and "set-cookie" not in home.headers


def test_missing_slash_redirects(running):
    resp = running.get("/apps/toy?x=1", headers=running.cookie)
    assert resp.status_code == 308
    assert resp.headers["location"] == "/apps/toy/?x=1"


def test_page_and_assets(running):
    page = running.get("/apps/toy/", headers=running.cookie)
    assert page.status_code == 200 and "style.css" in page.text
    css = running.get("/apps/toy/style.css", headers=running.cookie)
    assert css.status_code == 200 and css.headers["content-type"] == "text/css"


def test_child_gets_its_token_and_never_the_launcher_secret(running):
    headers = {
        **running.cookie,
        "origin": ORIGIN,
        "referer": ORIGIN + "/apps/toy/",
        SESSION_HEADER: running.security.secret,
        "x-forwarded-for": "10.0.0.1",
        TOKEN_HEADER: "forged",
        "sec-fetch-site": "same-origin",
    }
    resp = running.get("/apps/toy/api/echo?q=1", headers=headers)
    assert resp.status_code == 200
    seen = resp.json()
    assert seen["path"] == "/apps/toy/api/echo" and seen["query"] == "q=1"
    h = seen["headers"]
    assert h["host"] == f"127.0.0.1:{running.child.context.port}"
    assert h["x-toy-token"] == running.child.context.token
    assert h["sec-fetch-site"] == "same-origin"
    for name in ("cookie", "origin", "referer", SESSION_HEADER, "x-forwarded-for"):
        assert name not in h
    assert running.security.secret not in resp.text


def test_json_post(running):
    headers = {**running.cookie, "origin": ORIGIN}
    resp = running.post("/apps/toy/api/echo", json={"a": 1}, headers=headers)
    assert resp.status_code == 200
    assert resp.json()["method"] == "POST" and resp.json()["body_len"] == len('{"a":1}')


def test_redirects(running):
    absolute = running.get("/apps/toy/api/redirect-absolute", headers=running.cookie)
    assert absolute.status_code == 302 and absolute.headers["location"] == "/apps/toy/"
    relative = running.get("/apps/toy/api/redirect-relative", headers=running.cookie)
    assert relative.headers["location"] == "../style.css"


def test_large_upload_streams_through(running):
    body = b"x" * (5 << 20)
    headers = {**running.cookie, "origin": ORIGIN}
    resp = running.post("/apps/toy/api/echo", content=body, headers=headers)
    assert resp.status_code == 200 and resp.json()["body_len"] == len(body)


def test_upload_over_the_limit_is_413(running):
    headers = {**running.cookie, "origin": ORIGIN}
    resp = running.post("/apps/toy/api/echo", content=b"x" * (9 << 20), headers=headers)
    assert resp.status_code == 413

    def chunks():
        for _ in range(9):
            yield b"x" * (1 << 20)

    resp = running.post("/apps/toy/api/echo", content=chunks(), headers=headers)
    assert resp.status_code == 413


def test_cookies_and_cors_from_the_child_are_stripped(running):
    resp = running.get("/apps/toy/api/cookie", headers=running.cookie)
    assert resp.status_code == 200
    assert "set-cookie" not in resp.headers
    assert not any(k.lower().startswith("access-control-") for k in resp.headers)


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"cookie": "opncafyr_session_54322=wrong"},
        {"cookie": "opncafyr_session_1=whatever"},
    ],
)
def test_session_cookie_required(running, headers):
    assert running.get("/apps/toy/", headers=headers).status_code == 403


def test_session_header_alone_is_not_enough(running):
    headers = {SESSION_HEADER: running.security.secret}
    assert running.get("/apps/toy/", headers=headers).status_code == 403


@pytest.mark.parametrize("site", ["cross-site", "same-site"])
def test_cross_site_fetch_rejected(running, site):
    headers = {**running.cookie, "sec-fetch-site": site}
    assert running.get("/apps/toy/", headers=headers).status_code == 403


def test_navigation_allowed(running):
    headers = {**running.cookie, "sec-fetch-site": "none"}
    assert running.get("/apps/toy/", headers=headers).status_code == 200


@pytest.mark.parametrize("origin", [None, "https://evil.example", "http://127.0.0.1:1"])
def test_state_changing_needs_our_origin(running, origin):
    headers = dict(running.cookie)
    if origin:
        headers["origin"] = origin
    assert running.post("/apps/toy/api/echo", json={}, headers=headers).status_code == 403


def test_unknown_tool_404(running):
    assert running.get("/apps/nope/", headers=running.cookie).status_code == 404


def test_not_started_is_503_without_restart(env):
    resp = env.get("/apps/toy/", headers=env.cookie)
    assert resp.status_code == 503
    assert "restart" not in resp.text.lower()


def test_crash_shows_502_with_restart(running):
    running.get("/apps/toy/api/crash", headers=running.cookie)
    running.child.proc.wait(10)
    page = running.get("/apps/toy/", headers=running.cookie)
    assert page.status_code == 502
    assert "action='/apps/_opncafyr/toy/restart'" in page.text

    no_origin = running.post("/apps/_opncafyr/toy/restart", headers=running.cookie)
    assert no_origin.status_code == 403
    headers = {**running.cookie, "origin": ORIGIN}
    resp = running.post("/apps/_opncafyr/toy/restart", headers=headers)
    assert resp.status_code == 303 and resp.headers["location"] == "/apps/toy/"
    assert running.get("/apps/toy/", headers=running.cookie).status_code == 200


def test_api_apps_reports_state(running):
    apps = running.get("/api/apps", headers=running.cookie).json()["apps"]
    assert apps == [{"id": "toy", "route": "/apps/toy/", "state": "running"}]
