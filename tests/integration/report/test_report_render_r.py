"""A real quartifyr render of a two-analyte publication (#32)."""

from __future__ import annotations

import re
import struct
import subprocess
import zipfile
import zlib

import pytest

from opncafyr.core import runs as R
from opncafyr.core.manifest import validate
from opncafyr.core.rscript import rscript_command
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


# A formatted table as opnca-r 0.3.2 saves it (no caption), wide enough that reportifyr's
# default layout squeezed it (#62): eleven columns with header words up to eight letters.
MAKE_WIDE = """
suppressMessages(library(flextable))
dir <- Sys.getenv("MAKE_DIR")
cols <- c("Subject", "Arm", "Cmax", "Tmax", "AUClast", "AUCinf", "Lambdaz", "HalfLife",
          "CLF", "VF", "Dose")
df <- as.data.frame(setNames(rep(list(c(1.2345, 2.3456)), length(cols)), cols))
ft <- set_header_labels(flextable(df), values = as.list(setNames(paste0(cols, " (unit)"), cols)))
ft <- add_footer_lines(colformat_double(ft, digits = 4), values = "A footnote")
saveRDS(autofit(ft), file.path(dir, "wide.rds"))
"""


def _real_flextable(runtime, tmp_path) -> bytes:
    script = tmp_path / "make_wide.R"
    script.write_text(MAKE_WIDE, "utf-8")
    spec = rscript_command(runtime, script, env={"MAKE_DIR": str(tmp_path)})
    done = subprocess.run(  # noqa: S603 - the runtime's Rscript, a fixed script
        spec.argv,
        env={**spec.env, "PATH": str(runtime.executable("rscript").parent)},
        capture_output=True,
        text=True,
        check=False,
    )
    if done.returncode != 0:
        pytest.skip(f"flextable is not in the runtime library: {done.stderr[-200:]}")
    return (tmp_path / "wide.rds").read_bytes()


def test_a_wide_formatted_table_without_a_caption_fills_its_place_and_fits_the_page(
    api, runtime, tmp_path
):
    # #62: opnca-r 0.3.2 leaves the caption off and quartifyr 0.3.4 fits the table.
    rds = "results/tables/opnca-summary.rds"
    run, _ = completed_run(
        api.study, "Drug A", described=True, formatted={rds: _real_flextable(runtime, tmp_path)}
    )
    pub = api.post(
        "/api/nca/publications", json={"items": picks(run, ["summary", "summary-formatted"])}
    )
    assert pub.status_code == 201, pub.text
    pub = pub.json()["publication"]
    job = api.post("/api/nca/report", json={"publication_id": pub["publication_id"]}).json()
    assert wait_job(api, job["job_id"])["state"] == "completed"
    check = wait_job(api, api.post("/api/nca/report/check").json()["job_id"])
    assert check["state"] == "completed", check["message"]
    findings = api.get("/api/nca/report").json()["check"]["findings"]
    assert [f for f in findings if f["severity"] == "error"] == [], findings

    out = api.post("/api/nca/report/render", json={"status": "draft"}).json()
    done = wait_job(api, out["job_id"], timeout=900)
    assert done["state"] == "completed", done["message"]
    docx = api.study / "report" / "builds" / out["build_id"] / "report-draft.docx"
    with zipfile.ZipFile(docx) as z:
        body = z.read("word/document.xml").decode("utf-8")
    # Strict mode already rejects an unfilled placeholder, so a completed render has both
    # tables in it. The wide one fits the 6.5 inch content width (9360 twips) and keeps a
    # fixed layout, where reportifyr alone left it to the word processor.
    tables = re.findall(r"<w:tbl[ >].*?</w:tbl>", body, flags=re.S)
    wide = [t for t in tables if "HalfLife (unit)" in t]
    assert len(wide) == 1
    grid = [int(w) for w in re.findall(r'<w:gridCol w:w="(\d+)"', wide[0])]
    assert len(grid) == 11 and sum(grid) <= 9360, grid
    assert 'w:type="fixed"' in wide[0]


def _big_png(width: int = 2000, height: int = 1200) -> bytes:
    """A solid PNG with opnca-r's pixel size and no pixel density (#62)."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    rows = b"".join(b"\x00" + b"\xff\xff\xff" * width for _ in range(height))
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )


def test_a_2000_pixel_figure_is_sized_by_the_page_not_its_pixels(api):
    # #62: with use_artifact_size: true a 2000 px PNG came out about 27 inches wide.
    figure = "results/figures/profiles-linear.png"
    run, _ = completed_run(api.study, "Drug A", formatted={figure: _big_png()})
    pub = api.post("/api/nca/publications", json={"items": picks(run, ["profiles-linear"])})
    assert pub.status_code == 201, pub.text
    pub = pub.json()["publication"]
    job = api.post("/api/nca/report", json={"publication_id": pub["publication_id"]}).json()
    assert wait_job(api, job["job_id"])["state"] == "completed"
    out = api.post("/api/nca/report/render", json={"status": "draft"}).json()
    done = wait_job(api, out["job_id"], timeout=900)
    assert done["state"] == "completed", done["message"]
    docx = api.study / "report" / "builds" / out["build_id"] / "report-draft.docx"
    with zipfile.ZipFile(docx) as z:
        body = z.read("word/document.xml").decode("utf-8")
    widths = [int(w) for w in re.findall(r'<wp:extent cx="(\d+)"', body)]
    assert widths, "the figure is not in the document"
    assert max(widths) <= 5943600, widths  # 6.5 inches in EMU


def test_a_setting_saved_in_the_editor_changes_the_render_and_is_recorded(api):
    # #64: default_fig_width edited in the app is what reportifyr uses, and the build
    # records the file it used.
    figure = "results/figures/profiles-linear.png"
    run, _ = completed_run(api.study, "Drug A", formatted={figure: _big_png()})
    pub = api.post("/api/nca/publications", json={"items": picks(run, ["profiles-linear"])})
    assert pub.status_code == 201, pub.text
    pub = pub.json()["publication"]
    job = api.post("/api/nca/report", json={"publication_id": pub["publication_id"]}).json()
    assert wait_job(api, job["job_id"])["state"] == "completed"
    editor = "/api/tools/quartifyr/project"
    doc = api.post(f"{editor}/read", json={"path": "report/config.yaml"}).json()
    assert "default_fig_width: 6" in doc["text"]
    saved = api.post(
        f"{editor}/save",
        json={
            "path": "report/config.yaml",
            "text": doc["text"].replace("default_fig_width: 6", "default_fig_width: 3"),
            "expected_sha256": doc["sha256"],
        },
    )
    assert saved.status_code == 200 and saved.json()["warnings"] == [], saved.text
    out = api.post("/api/nca/report/render", json={"status": "draft"}).json()
    done = wait_job(api, out["job_id"], timeout=900)
    assert done["state"] == "completed", done["message"]
    build = api.get("/api/nca/report").json()["builds"][0]
    config = next(i for i in build["inputs"] if i["role"] == "config")
    assert config["sha256"] == saved.json()["sha256"]
    docx = api.study / "report" / "builds" / out["build_id"] / "report-draft.docx"
    with zipfile.ZipFile(docx) as z:
        body = z.read("word/document.xml").decode("utf-8")
    widths = [int(w) for w in re.findall(r'<wp:extent cx="(\d+)"', body)]
    assert widths and max(widths) <= 3 * 914400, widths
