import pytest
from fastapi.testclient import TestClient

from opncafyr.core.app import create_app
from opncafyr.core.security import SESSION_HEADER, SecurityConfig

PORT = 54321


@pytest.fixture
def security() -> SecurityConfig:
    return SecurityConfig(port=PORT)


@pytest.fixture
def client(security: SecurityConfig):
    calls: list[str] = []
    app = create_app(security, request_shutdown=lambda: calls.append("quit"))
    with TestClient(app, base_url=f"http://127.0.0.1:{PORT}") as c:
        c.shutdown_calls = calls
        c.session_headers = {SESSION_HEADER: security.secret}
        yield c
