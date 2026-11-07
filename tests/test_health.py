from tool_sets import ALL_TOOLS


def test_health_ok(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert len(body["instance_id"]) == 32


def test_home_embeds_session_secret(client, security):
    resp = client.get("/")
    assert resp.status_code == 200
    assert security.secret in resp.text
    assert "Research use only" in resp.text


def test_tools_lists_installed_adapters(client):
    ids = {t["id"] for t in client.get("/api/tools").json()["tools"]}
    assert ids == ALL_TOOLS


def test_no_cors_headers(client):
    resp = client.get("/health", headers={"Origin": "http://127.0.0.1:54321"})
    assert "access-control-allow-origin" not in resp.headers
