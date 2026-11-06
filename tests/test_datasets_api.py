"""Datasets API: scan, preview and import in the open workspace (#21)."""

import hashlib
import json
import logging
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from opncafyr.core.app import create_app
from opncafyr.core.jobmanager import JobManager
from opncafyr.core.security import SESSION_HEADER
from opncafyr.core.workspace import RecentWorkspaces

FIXTURES = Path(__file__).parent / "fixtures"
EXPECTED = json.loads((FIXTURES / "two_analyte.expected.json").read_text("utf-8"))


@pytest.fixture
def study(tmp_path):
    root = tmp_path / "Study"
    (root / "source-data").mkdir(parents=True)
    shutil.copyfile(FIXTURES / "two_analyte.csv", root / "two_analyte.csv")
    shutil.copyfile(FIXTURES / "two_analyte_mixed_units.csv", root / "source-data" / "mixed.csv")
    (root / "source-data" / "old").mkdir()
    (root / "source-data" / "old" / "deep.csv").write_text("A,B\n1,2\n", "utf-8")
    return root.resolve()


@pytest.fixture
def api(security, tmp_path, study):
    jobs = JobManager(poll_interval=0.05)
    app = create_app(
        security,
        jobs=jobs,
        picker=lambda prompt, start: study,
        recent=RecentWorkspaces(tmp_path / "state" / "recent.json"),
    )
    with TestClient(
        app,
        base_url=f"http://127.0.0.1:{security.port}",
        cookies={security.cookie_name: security.secret},
    ) as client:
        client.headers[SESSION_HEADER] = security.secret
        yield client
    jobs.shutdown()


def _open(api):
    folder = api.post("/api/workspace/pick").json()["folder"]
    api.post("/api/workspace/init", json={"path": folder["path"]}).raise_for_status()


def test_no_workspace(api):
    data = api.get("/api/datasets").json()
    assert data["workspace_open"] is False and data["files"] == []
    assert data["formats"] == [{"id": "csv", "label": "CSV", "extensions": [".csv"]}]
    r = api.post("/api/datasets/preview", json={"path": "two_analyte.csv"})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "no_workspace"


def test_scan_lists_the_allowed_levels(api):
    _open(api)
    data = api.get("/api/datasets").json()
    assert data["workspace_open"] is True
    assert data["folders"] == [".", "source-data"]
    files = {f["path"]: f for f in data["files"]}
    assert sorted(files) == ["source-data/mixed.csv", "two_analyte.csv"]
    main = files["two_analyte.csv"]
    assert main["row_count"] == EXPECTED["files"]["two_analyte.csv"]["rows"]
    assert main["columns"] == EXPECTED["files"]["two_analyte.csv"]["columns"]
    assert main["imported"] is False


def test_preview_of_two_analyte_lists_both_analytes(api):
    _open(api)
    r = api.post("/api/datasets/preview", json={"path": "two_analyte.csv", "rows": 10})
    assert r.status_code == 200
    d = r.json()["dataset"]
    assert d["row_count"] == 168 and d["row_count_exact"] is True
    assert len(d["head"]) == 10
    assert d["details"]["delimiter"] == ","
    conc = next(c for c in d["columns"] if c["name"] == "CONC")
    assert conc["type"] == "character" and conc["near_type"] == "number"
    assert conc["misfits"] == ["<LLOQ", "12,4"]

    (column,) = d["annotations"]["analytes"]["columns"]
    expected = EXPECTED["files"]["two_analyte.csv"]["analytes"]
    assert column["column"] == expected["column"]
    got = {v["value"]: v["rows"] for v in column["values"]}
    assert got == {value: info["rows"] for value, info in expected["values"].items()}
    assert {"PARENT", "METAB"} <= set(got)
    assert column["variants"] == [["METAB", "METAB "]]


@pytest.mark.parametrize(
    ("path", "status", "code"),
    [
        ("source-data/old/deep.csv", 403, "not_allowed"),
        ("../x.csv", 400, "invalid_path"),
        ("nothing.csv", 404, "not_found"),
        (".opncafyr/workspace.json", 403, "not_allowed"),
    ],
)
def test_preview_refusals(api, path, status, code):
    _open(api)
    r = api.post("/api/datasets/preview", json={"path": path})
    assert r.status_code == status
    assert r.json()["detail"]["code"] == code


def test_preview_and_import_need_the_session_secret(api):
    _open(api)
    for endpoint in ("preview", "import"):
        r = api.post(
            f"/api/datasets/{endpoint}",
            json={"path": "two_analyte.csv"},
            headers={SESSION_HEADER: "wrong"},
        )
        assert r.status_code == 403, endpoint


def test_import_records_the_source(api, study):
    _open(api)
    sha = hashlib.sha256((study / "two_analyte.csv").read_bytes()).hexdigest()
    r = api.post("/api/datasets/import", json={"path": "two_analyte.csv"})
    assert r.status_code == 200
    body = r.json()
    assert body["created"] is True
    assert body["source"]["path"] == "two_analyte.csv"
    assert body["source"]["sha256"] == sha
    doc = json.loads((study / ".opncafyr" / "workspace.json").read_text("utf-8"))
    assert doc["sources"] == [body["source"]]
    again = api.post("/api/datasets/import", json={"path": "two_analyte.csv"}).json()
    assert again == {"source": body["source"], "created": False}
    files = {f["path"]: f for f in api.get("/api/datasets").json()["files"]}
    assert files["two_analyte.csv"]["imported"] is True
    assert files["source-data/mixed.csv"]["imported"] is False


def test_no_data_values_are_logged(api, caplog):
    caplog.set_level(logging.DEBUG)
    _open(api)
    api.get("/api/datasets")
    api.post("/api/datasets/preview", json={"path": "two_analyte.csv", "rows": 200})
    api.post("/api/datasets/import", json={"path": "two_analyte.csv"})
    text = "\n".join(r.getMessage() for r in caplog.records)
    for value in ("TA-001", "Haemolysed", "<LLOQ", "PARENT"):
        assert value not in text
