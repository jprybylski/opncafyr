"""The NCA results deck with the real deckifyr: editor under /apps/deckifyr/, build (#33)."""

from __future__ import annotations

import re
import zipfile

import pytest
import yaml

from opncafyr.core import runs as R
from opncafyr.core.manifest import validate
from publish_helpers import completed_run, picks, publish_client, wait_job, writable

pytestmark = pytest.mark.integration


@pytest.fixture
def api(security, tmp_path, runtime):
    study = (tmp_path / "Study").resolve()
    study.mkdir()
    with publish_client(security, tmp_path, study, runtime) as client:
        yield client
        client.app.state.supervisor.stop_all()
    writable(study)


@pytest.fixture
def deck(api):
    a, _ = completed_run(api.study, "Drug A")
    b, _ = completed_run(api.study, "Metabolite B")
    pub = api.post("/api/nca/publications", json={"items": picks(a) + picks(b, ["summary"])})
    assert pub.status_code == 201, pub.text
    pub = pub.json()["publication"]
    out = api.post("/api/nca/deck", json={"publication_id": pub["publication_id"]}).json()
    job = wait_job(api, out["job_id"], 120)
    assert job["state"] == "completed", job["message"]
    return pub, a, b


def test_generated_deck_validates_and_builds(api, deck):
    pub, a, b = deck
    check = wait_job(api, api.post("/api/nca/deck/check").json()["job_id"], 120)
    assert check["state"] == "completed", check["message"]
    findings = api.get("/api/nca/deck").json()["check"]["findings"]
    assert [f for f in findings if f["severity"] == "error"] == [], findings
    r = api.post("/api/nca/deck/build", json={"previews": False})
    assert r.status_code == 200, r.text
    job = wait_job(api, r.json()["job_id"], 300)
    assert job["state"] == "completed", job["message"]
    build = api.get("/api/nca/deck").json()["builds"][0]
    validate(build, "publish-build-v1")
    assert sorted(build["runs"]) == sorted([a, b])
    assert [i["sha256"] for i in build["items"]] == [i["published_sha256"] for i in pub["items"]]
    assert build["tools"]["deckifyr"] == "0.3.6"
    pptx = api.study / "deck" / "builds" / build["build_id"] / "nca-results.pptx"
    assert R.sha256_file(pptx) == next(o["sha256"] for o in build["outputs"] if o["role"] == "pptx")
    with zipfile.ZipFile(pptx) as z:
        slides = [n for n in z.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)]
        text = " ".join(z.read(n).decode("utf-8") for n in slides)
    assert len(slides) == 1 + len(pub["items"])
    assert "Metabolite B" in text and "Source: .opncafyr/runs/" in text  # sidecar footers


def test_editor_under_the_launcher_origin_and_unsaved_edits_survive_closing(api, deck):
    r = api.post("/api/nca/deck/editor")
    assert r.status_code == 200, r.text
    base = str(api.base_url).rstrip("/")
    page = api.get("/apps/deckifyr/")
    assert page.status_code == 200 and "text/html" in page.headers["content-type"]
    assert api.get("/apps/deckifyr/api/project").status_code == 200
    path = api.study / "deck" / "presentation.yaml"
    slide = yaml.safe_load(path.read_text("utf-8"))["slides"][1]["id"]
    edit = api.patch(
        f"/apps/deckifyr/api/slides/{slide}/elements/title",
        json={"value": "Edited in the editor"},
        headers={"origin": base},
    )
    assert edit.status_code == 200, edit.text
    # Unsaved edits block a build until they are saved.
    blocked = api.post("/api/nca/deck/build", json={"previews": False})
    assert blocked.status_code == 409 and blocked.json()["detail"]["code"] == "editor_unsaved"
    # Closing the editor saves them (if_dirty: save), so nothing is lost.
    assert api.delete("/api/nca/deck/editor").status_code == 200
    doc = yaml.safe_load(path.read_text("utf-8"))
    titles = [s["elements"]["title"]["value"] for s in doc["slides"] if s["id"] == slide]
    assert titles == ["Edited in the editor"]
    assert api.get("/api/nca/deck").json()["edited"] == ["presentation.yaml"]


def test_build_with_previews_when_libreoffice_works(api, deck, runtime):
    if not runtime.has("soffice"):
        pytest.skip("the runtime has no LibreOffice")
    probe = api.post("/api/nca/deck/previews").json()
    if not probe["available"]:
        pytest.skip(probe["message"])
    r = api.post("/api/nca/deck/build", json={"previews": True})
    assert r.status_code == 200, r.text
    job = wait_job(api, r.json()["job_id"], 600)
    assert job["state"] == "completed", job["message"]
    build = api.get("/api/nca/deck").json()["builds"][0]
    previews = [o for o in build["outputs"] if o["role"] == "preview"]
    assert len(previews) == 1 + len(deck[0]["items"])
    folder = api.study / "deck" / "builds" / build["build_id"]
    for o in previews:
        assert R.sha256_file(folder / o["path"]) == o["sha256"]
