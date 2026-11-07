"""A real quartifyr render of a two-analyte publication (#32)."""

from __future__ import annotations

import zipfile

import pytest

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
    writable(study)


def test_two_analyte_publication_renders_to_docx(api):
    a, _ = completed_run(api.study, "Drug A")
    b, _ = completed_run(api.study, "Metabolite B")
    pub = api.post("/api/nca/publications", json={"items": picks(a) + picks(b, ["summary"])})
    assert pub.status_code == 201, pub.text
    pub = pub.json()["publication"]

    job = api.post("/api/nca/report", json={"publication_id": pub["publication_id"]}).json()
    done = wait_job(api, job["job_id"])
    assert done["state"] == "completed", done["message"]
    project = api.study / "report"
    for rel in ("report.qmd", "_quarto.yml", ".report_init.json", "org-reference.docx"):
        assert (project / rel).is_file(), rel
    assert sorted(p.name for p in (project / "OUTPUTS" / "tables").iterdir())  # the copy

    # Edit a prose paragraph through the launcher's editor (#52), as the page does.
    editor = "/api/tools/quartifyr/project"
    doc = api.post(f"{editor}/read", json={"path": "report.qmd"}).json()
    assert "This report was assembled by opncafyr" in doc["text"]
    edited = doc["text"].replace(
        "This report was assembled by opncafyr", "Edited in the launcher, this report was built"
    )
    saved = api.post(
        f"{editor}/save",
        json={"path": "report.qmd", "text": edited, "expected_sha256": doc["sha256"]},
    )
    assert saved.status_code == 200, saved.text

    check = wait_job(api, api.post("/api/nca/report/check").json()["job_id"])
    assert check["state"] == "completed", check["message"]
    findings = api.get("/api/nca/report").json()["check"]["findings"]
    assert [f for f in findings if f["severity"] == "error"] == [], findings

    out = api.post("/api/nca/report/render", json={"status": "draft"}).json()
    done = wait_job(api, out["job_id"], timeout=900)
    assert done["state"] == "completed", done["message"]
    build = api.get("/api/nca/report").json()["builds"][0]
    validate(build, "publish-build-v1")
    assert build["build_id"] == out["build_id"]
    assert sorted(build["runs"]) == sorted([a, b])
    assert {i["published_path"] for i in build["items"]} == {
        i["published_path"] for i in pub["items"]
    }
    roles = {i["role"] for i in build["inputs"]}
    shell = next(i for i in build["inputs"] if i["role"] == "shell_qmd")
    assert shell["sha256"] == saved.json()["sha256"]
    assert {"shell_qmd", "quarto_yml", "reference_doc", "extensions", "sidecar"} <= roles
    assert build["tools"]["quartifyr"] and build["tools"]["reportifyr"]

    r = api.get(f"/api/nca/report/builds/{out['build_id']}/docx")
    assert r.status_code == 200
    docx = api.study / "report" / "builds" / out["build_id"] / "report-draft.docx"
    assert R.sha256_file(docx) == build["outputs"][0]["sha256"]
    with zipfile.ZipFile(docx) as z:
        body = z.read("word/document.xml").decode("utf-8")
    assert "Edited in the launcher" in body
    assert "Metabolite B" in body and len([n for n in z.namelist() if "media/" in n]) == 2
    # Only the selected publication's files went in: nothing else under report/OUTPUTS.
    published = {p.rpartition("/")[2] for i in pub["items"] for p in (i["published_path"],)}
    copies = {p.name for p in (project / "OUTPUTS").rglob("*") if p.is_file()}
    assert {n for n in copies if not n.endswith("_metadata.json")} == published


def test_a_missing_sidecar_fails_the_render(api):
    a, _ = completed_run(api.study, "Drug A")
    pub = api.post("/api/nca/publications", json={"items": picks(a, ["summary"])}).json()
    pub = pub["publication"]
    job = api.post("/api/nca/report", json={"publication_id": pub["publication_id"]}).json()
    assert wait_job(api, job["job_id"])["state"] == "completed"
    (api.study / pub["items"][0]["sidecar_path"]).unlink()
    r = api.post("/api/nca/report/render", json={"status": "draft"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "publication_changed"
    assert api.get("/api/nca/report").json()["builds"] == []
