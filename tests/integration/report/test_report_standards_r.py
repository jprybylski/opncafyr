"""A real quartifyr render from an imported report template (#70, ADR 0011).

The template carries quartifyr's reference DOCX, footnotes with an extra abbreviation, the
bundled settings and a partial quartifyr ``style.yaml``, which the render merges over
quartifyr's default style (``--style`` default, ``--override`` the project's file) beside
``--reference-doc``.
"""

from __future__ import annotations

import json
import zipfile

import pytest

from opncafyr.adapters import quartifyr as Q
from opncafyr.core import runs as R
from publish_helpers import completed_run, picks, publish_client, wait_job, writable
from standards_helpers import CONFIG, FOOTNOTES, write_bundle

pytestmark = pytest.mark.integration

HOUSE_FOOTNOTES = FOOTNOTES.replace(
    b"abbreviations:\n", b"abbreviations:\n HOUSE: House style\n", 1
)
STYLE = b"""\
fonts:
  body: "Arial"
  heading: "Arial"
equation:
  font: "STIX Two Math"
"""


@pytest.fixture
def api(security, tmp_path, runtime):
    study = (tmp_path / "Study").resolve()
    study.mkdir()
    with publish_client(security, tmp_path, study, runtime) as client:
        yield client
    writable(study)


def test_a_report_renders_from_an_imported_template_with_its_style(api, runtime, tmp_path):
    docx = Q.reference_docx(runtime).read_bytes()
    src = write_bundle(
        tmp_path / "house",
        [
            {
                "id": "house-report",
                "type": "report-template",
                "title": "House report",
                "files": {
                    "report/org-reference.docx": docx,
                    "report/standard_footnotes.yaml": HOUSE_FOOTNOTES,
                    "report/config.yaml": CONFIG,
                    "report/style.yaml": STYLE,
                },
            }
        ],
    )
    preview = api.post(
        "/api/standards/inspect", json={"source": {"kind": "path", "path": str(src)}}
    ).json()
    assert preview["problems"] == [], preview["problems"]
    done = api.post(
        "/api/standards/install",
        json={"preview_id": preview["preview_id"], "select": ["house-report"]},
    )
    assert done.status_code == 200, done.text

    a, _ = completed_run(api.study, "Drug A")
    pub = api.post("/api/nca/publications", json={"items": picks(a, ["summary"])})
    assert pub.status_code == 201, pub.text
    pub = pub.json()["publication"]
    job = api.post("/api/nca/report", json={"publication_id": pub["publication_id"]}).json()
    setup = wait_job(api, job["job_id"])
    assert setup["state"] == "completed", setup["message"]
    project = api.study / "report"
    assert (project / Q.STYLE).read_bytes() == STYLE
    assert (project / Q.FOOTNOTES).read_bytes() == HOUSE_FOOTNOTES

    out = api.post("/api/nca/report/render", json={"status": "draft"}).json()
    rendered = wait_job(api, out["job_id"], timeout=900)
    assert rendered["state"] == "completed", rendered["message"]
    folder = project / "builds" / out["build_id"]
    build = json.loads((folder / "build.json").read_text("utf-8"))
    roles = {i["role"]: i for i in build["inputs"]}
    assert roles["style"]["sha256"] == R.sha256_file(project / Q.STYLE)
    assert build["standards"]["component"] == "house-report"
    assert build["standards"]["snapshot"] == done.json()["snapshot"]
    with zipfile.ZipFile(folder / "report-draft.docx") as z:
        settings = z.read("word/settings.xml").decode("utf-8")
    assert "STIX Two Math" in settings  # the template's style reached the document
