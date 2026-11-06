"""#16: deckifyr 0.3.5 behind /apps/deckifyr/, including the attacks from the upstream review."""

from __future__ import annotations

import time

import httpx
import pytest
from proxy_harness import EVIL, assert_stays_under, load_page, tree_pids, wait_gone

pytestmark = pytest.mark.integration
ROUTE = "/apps/deckifyr/"
API = ROUTE + "api/"

# The smallest project that builds with only the core dependencies: no Quarto fragments,
# no reportifyr content, previews off (so neither Quarto nor LibreOffice is needed).
PROJECT = {
    "design.yaml": (
        "deckifyr: '0.1'\n"
        "slide: {width: 10in, height: 5.625in}\n"
        "fonts: {body: Arial, heading: Arial}\n"
    ),
    "layouts.yaml": "deckifyr: '0.1'\nlayouts: {blank: {}}\n",
    "presentation.yaml": (
        "deckifyr: '0.1'\n"
        "design: {base: design.yaml}\n"
        "layouts: layouts.yaml\n"
        "metadata: {title: Proxy harness}\n"
        "build: {output: build/deck.pptx, previews: false}\n"
        "slides:\n"
        "  - id: only-slide\n"
        "    layout: blank\n"
        "    elements: {}\n"
    ),
}


@pytest.fixture(scope="module")
def session(runtime, tmp_path_factory):
    if not runtime.has("deckifyr_python"):
        pytest.skip("runtime has no deckifyr")
    project = tmp_path_factory.mktemp("deck")
    for name, text in PROJECT.items():
        (project / name).write_text(text)
    return {"project": str(project)}


@pytest.fixture(scope="module")
def child(controller, session):
    child = controller.supervisor.start("deckifyr", session)
    yield child
    controller.supervisor.stop("deckifyr")


def test_page_and_assets_stay_under_the_prefix(browser, controller, child):
    record = load_page(browser, controller, ROUTE)
    assert_stays_under(record, controller, ROUTE)
    assert any("/api/" in u for u in record.requests), "the page made no API calls"


def test_save_build_and_download(controller, child):
    with controller.client() as c:
        assert c.post(API + "save").status_code == 200
        start = c.post(API + "build")
        assert start.status_code == 200, start.text
        job_id = start.json()["job_id"]
        deadline = time.monotonic() + 180
        while True:
            job = c.get(API + f"jobs/{job_id}").json()
            if job["status"] not in ("queued", "running") or time.monotonic() > deadline:
                break
            time.sleep(0.5)
        assert job["status"] == "succeeded", job
        pptx = c.get(API + f"jobs/{job_id}/artifacts/pptx")
        assert pptx.status_code == 200
        assert pptx.content[:2] == b"PK"


def test_proxy_needs_the_session(controller, child):
    assert httpx.get(controller.origin + API + "project").status_code == 403
    with controller.client() as c:
        assert c.get(API + "project", headers={"sec-fetch-site": "same-site"}).status_code == 403
        assert c.post(API + "build", headers={"origin": EVIL}).status_code == 403


def test_direct_child_port_attacks_fail(child):
    origin = child.context.origin
    token = child.spec.auth_headers
    # A cross-site form POST (the reproduced attack built the deck).
    form = httpx.post(
        origin + API + "build",
        data={"x": "1"},
        headers={"origin": EVIL, "sec-fetch-site": "cross-site"},
    )
    assert form.status_code == 403
    # A foreign Host (DNS rebinding), even with the right token.
    rebind = httpx.get(origin + API + "project", headers={**token, "host": "evil.example"})
    assert rebind.status_code == 421
    # No token at all.
    assert httpx.get(origin + API + "project").status_code == 403
    assert httpx.get(origin + API + "project", headers=token).status_code == 200


def test_orderly_stop_and_tree_kill(controller, session):
    sup = controller.supervisor
    child = sup.start("deckifyr", session)
    procs = tree_pids(child.pid)
    sup.stop("deckifyr")
    assert child.exit_code == 0
    assert not child.context.workdir.exists()
    assert wait_gone(procs) == []

    child = sup.start("deckifyr", session)
    procs = tree_pids(child.pid)
    child.proc.kill_tree()
    assert wait_gone(procs) == []
    sup.stop("deckifyr")
