import pytest
from fastapi.testclient import TestClient

from opncafyr.core.app import create_app
from opncafyr.core.bootstrap import mint_open_token
from opncafyr.core.security import SESSION_HEADER

PORT = 54321
ORIGIN = f"http://127.0.0.1:{PORT}"


@pytest.mark.parametrize("host", ["evil.example:54321", "127.0.0.1:1", "0.0.0.0:54321", ""])
def test_foreign_host_rejected(client, host):
    assert client.get("/health", headers={"Host": host}).status_code == 400


def test_localhost_alias_allowed(client):
    assert client.get("/health", headers={"Host": "localhost:54321"}).status_code == 200


def test_post_without_secret_rejected(client):
    assert client.post("/api/quit").status_code == 403
    assert client.shutdown_calls == []


def test_post_with_wrong_secret_rejected(client):
    resp = client.post("/api/quit", headers={"x-opncafyr-session": "nope"})
    assert resp.status_code == 403


def test_cross_origin_post_rejected_even_with_secret(client):
    headers = {**client.session_headers, "Origin": "https://evil.example"}
    assert client.post("/api/quit", headers=headers).status_code == 403
    assert client.shutdown_calls == []


def test_post_with_secret_and_same_origin_allowed(client):
    headers = {**client.session_headers, "Origin": "http://127.0.0.1:54321"}
    assert client.post("/api/quit", headers=headers).status_code == 200
    assert client.shutdown_calls == ["quit"]


def test_secret_differs_per_launch():
    from opncafyr.core.security import SecurityConfig

    assert SecurityConfig(port=1).secret != SecurityConfig(port=1).secret


# --- the session cookie and the one-time open link -------------------------------------


@pytest.fixture
def bare(security, tmp_path):
    """A client with no session cookie, like another process on this machine."""
    app = create_app(security, open_tokens=tmp_path / "tokens")
    with TestClient(app, base_url=ORIGIN, follow_redirects=False) as c:
        c.tokens = tmp_path / "tokens"
        yield c


@pytest.mark.parametrize("path", ["/", "/api/tools", "/api/jobs", "/api/apps", "/static/app.css"])
def test_everything_but_health_needs_the_cookie(bare, security, path):
    resp = bare.get(path)
    assert resp.status_code == 403
    assert security.secret not in resp.text
    assert bare.get(path, headers={"cookie": f"{security.cookie_name}=wrong"}).status_code == 403


def test_home_without_a_session_explains_how_to_reopen(bare, security):
    resp = bare.get("/")
    assert resp.status_code == 403
    assert resp.headers["content-type"].startswith("text/html")
    assert "Open opncafyr from its shortcut" in resp.text
    assert resp.headers["cache-control"] == "no-store"


def test_health_is_open_and_says_nothing_else(bare):
    resp = bare.get("/health")
    assert resp.status_code == 200
    assert set(resp.json()) == {"status", "version", "instance_id", "uptime_s"}


def test_open_link_sets_the_cookie_once(bare, security):
    link = f"/open?token={mint_open_token(bare.tokens)}"
    resp = bare.get(link)
    assert resp.status_code == 303 and resp.headers["location"] == "/"
    assert resp.headers["referrer-policy"] == "no-referrer"
    assert bare.cookies.get(security.cookie_name) == security.secret
    home = bare.get("/")
    assert home.status_code == 200 and security.secret in home.text

    # Used again, by a browser without the session, the link is dead.
    bare.cookies.clear()
    used = bare.get(link)
    assert used.status_code == 403
    assert "set-cookie" not in used.headers
    assert security.secret not in used.text


@pytest.mark.parametrize("token", ["", "nope", "A" * 43])
def test_bad_open_tokens_are_refused(bare, token):
    resp = bare.get(f"/open?token={token}")
    assert resp.status_code == 403
    assert "set-cookie" not in resp.headers


def test_open_refuses_cross_site_requests(bare):
    token = mint_open_token(bare.tokens)
    resp = bare.get(f"/open?token={token}", headers={"sec-fetch-site": "cross-site"})
    assert resp.status_code == 403


@pytest.mark.parametrize("site", ["cross-site", "same-site"])
def test_cross_site_api_reads_are_refused(client, site):
    assert client.get("/api/tools", headers={"sec-fetch-site": site}).status_code == 403


def test_secret_header_without_the_cookie_is_refused(bare, security):
    headers = {SESSION_HEADER: security.secret, "Origin": ORIGIN}
    assert bare.post("/api/quit", headers=headers).status_code == 403


ALL_REQUESTS = [
    ("GET", "/", {}),
    ("GET", "/health", {"Origin": ORIGIN}),
    ("GET", "/api/tools", {"Origin": "https://evil.example"}),
    ("GET", "/open?token=nope", {}),
    ("POST", "/api/quit", {"Origin": "https://evil.example"}),
    (
        "OPTIONS",
        "/api/quit",
        {"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"},
    ),
    ("GET", "/api/tools", {"Host": "evil.example"}),
]


@pytest.mark.parametrize(("method", "path", "headers"), ALL_REQUESTS)
@pytest.mark.parametrize("with_cookie", [True, False])
def test_no_cors_headers_ever(bare, security, method, path, headers, with_cookie):
    if with_cookie:
        headers = {**headers, "cookie": f"{security.cookie_name}={security.secret}"}
    resp = bare.request(method, path, headers=headers)
    assert not [k for k in resp.headers if k.lower().startswith("access-control-")]
