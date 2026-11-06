import json

import pytest
from fastapi.testclient import TestClient

from installer_world import PLATFORM, World, fake_steps, make_world
from opncafyr.core.app import create_app
from opncafyr.core.manifest import validate
from opncafyr.core.security import SESSION_HEADER, SecurityConfig
from opncafyr.installer import install as install_mod
from opncafyr.installer.install import FAIL_ENV
from opncafyr.installer.layout import Layout

PORT = 54321


@pytest.fixture
def security() -> SecurityConfig:
    return SecurityConfig(port=PORT)


@pytest.fixture
def client(security: SecurityConfig):
    calls: list[str] = []
    app = create_app(security, request_shutdown=lambda: calls.append("quit"))
    with TestClient(
        app,
        base_url=f"http://127.0.0.1:{PORT}",
        cookies={security.cookie_name: security.secret},
    ) as c:
        c.shutdown_calls = calls
        c.session_headers = {SESSION_HEADER: security.secret}
        yield c


# --- installer (#19, #36): a fake world and fakes for the parts that start real tools -----


@pytest.fixture
def world(tmp_path) -> World:
    return make_world(tmp_path)


@pytest.fixture
def layout(tmp_path) -> Layout:
    return Layout(tmp_path / "app root", tmp_path / "rroot")


@pytest.fixture
def fakes(monkeypatch):
    """Replace the parts that start real tools; record what ran."""
    staged: list[str] = []
    selfchecks: list[dict] = []
    icons_made: list[str] = []
    monkeypatch.setattr(install_mod.platforms, "detect", lambda: PLATFORM)
    monkeypatch.setattr(install_mod.platforms, "check_os_version", lambda p: "test OS")
    monkeypatch.setattr(install_mod, "steps", fake_steps(staged))
    monkeypatch.setattr(
        install_mod.smoke,
        "run_all",
        lambda *a, **k: [{"name": "fake", "status": "ok", "seconds": 0.0, "detail": "ok"}],
    )

    def selfcheck(ctx, draft):
        # The real one runs in the new launcher venv; here, validate the same records.
        version_dir = ctx.dir
        doc = json.loads(draft.read_text("utf-8"))
        validate(doc, "install-v1")
        validate(json.loads((version_dir / "runtime" / "runtime.json").read_text()), "runtime-v1")
        selfchecks.append(doc)

    monkeypatch.setattr(install_mod, "run_selfcheck", selfcheck)
    monkeypatch.setattr(
        install_mod.icons, "create", lambda layout, i, v, log: icons_made.append(i) or []
    )
    monkeypatch.setattr(install_mod.icons, "present", lambda layout: bool(icons_made))
    monkeypatch.delenv(FAIL_ENV, raising=False)
    return {"staged": staged, "selfchecks": selfchecks, "icons": icons_made}
