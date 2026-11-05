import pytest


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
