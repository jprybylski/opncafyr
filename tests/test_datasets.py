"""Shallow workspace scan, read-only preview and source import (#21)."""

import hashlib
import os
import shutil
from pathlib import Path

import pytest

from opncafyr.core.datasets import DatasetError, DatasetService
from opncafyr.core.registry import load_workflows
from opncafyr.core.workspace import add_source, init_workspace
from opncafyr.paths import WORKSPACE_META_DIR
from opncafyr.workflows.nca.importers.csv import CsvImporter

FIXTURES = Path(__file__).parent / "fixtures"
CSV = b"USUBJID,PCTESTCD,CONC\nS-1,PARENT,1.5\n"


class RecordingImporter(CsvImporter):
    def __init__(self):
        self.read_paths = []

    def read(self, path, **kwargs):
        self.read_paths.append(Path(path))
        return super().read(path, **kwargs)


def _service(importer=None):
    packs = load_workflows()
    annotators = [a for p in packs for a in p.annotators]
    return DatasetService([importer or CsvImporter()], annotators)


@pytest.fixture
def nested(tmp_path):
    root = tmp_path / "Study"
    for rel in [
        "a.csv",
        "B.CSV",
        "source-data/b.csv",
        "source-data/deep/c.csv",
        "sub/d.csv",
        f"{WORKSPACE_META_DIR}/x.csv",
        "source-data/.hidden.csv",
        ".e.csv",
    ]:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(CSV)
    (root / "notes.txt").write_text("not data", "utf-8")
    return root.resolve()


def _snapshot(root: Path) -> dict[str, tuple[str, int]]:
    out = {}
    for path in root.rglob("*"):
        if path.is_file():
            st = path.stat()
            out[path.relative_to(root).as_posix()] = (
                hashlib.sha256(path.read_bytes()).hexdigest(),
                st.st_mtime_ns,
            )
    return out


def test_scan_reads_only_the_root_and_source_data(nested):
    importer = RecordingImporter()
    files = _service(importer).scan(nested)
    assert [f["path"] for f in files] == ["a.csv", "B.CSV", "source-data/b.csv"]
    assert sorted(importer.read_paths) == sorted(
        [nested / "a.csv", nested / "B.CSV", nested / "source-data" / "b.csv"]
    )
    first = files[0]
    assert first["folder"] == "." and files[2]["folder"] == "source-data"
    assert first["columns"] == ["USUBJID", "PCTESTCD", "CONC"]
    assert first["row_count"] == 1 and first["row_count_exact"]
    assert first["size_bytes"] == len(CSV)
    assert first["sha256"] == hashlib.sha256(CSV).hexdigest()
    assert "head" not in first  # the scan carries no data rows


def test_scan_without_source_data(tmp_path):
    (tmp_path / "pk.csv").write_bytes(CSV)
    assert [f["path"] for f in _service().scan(tmp_path)] == ["pk.csv"]


@pytest.mark.parametrize(
    ("requested", "code"),
    [
        ("source-data/deep/c.csv", "not_allowed"),
        ("sub/d.csv", "not_allowed"),
        (f"{WORKSPACE_META_DIR}/x.csv", "not_allowed"),
        ("source-data/.hidden.csv", "not_allowed"),
        ("../outside.csv", "invalid_path"),
        ("/etc/passwd", "invalid_path"),
        ("notes.txt", "unsupported"),
        ("missing.csv", "not_found"),
    ],
)
def test_preview_refuses_other_files(nested, requested, code):
    (nested.parent / "outside.csv").write_bytes(CSV)
    importer = RecordingImporter()
    with pytest.raises(DatasetError) as err:
        _service(importer).preview(nested, requested)
    assert err.value.code == code
    assert importer.read_paths == []


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="no symlinks")
def test_symlink_out_of_the_workspace_is_not_listed(nested, tmp_path):
    outside = tmp_path / "outside.csv"
    outside.write_bytes(CSV)
    try:
        (nested / "link.csv").symlink_to(outside)
    except OSError:
        pytest.skip("symlinks not permitted")
    assert "link.csv" not in [f["path"] for f in _service().scan(nested)]
    with pytest.raises(DatasetError) as err:
        _service().preview(nested, "link.csv")
    assert err.value.code == "invalid_path"


def test_preview_returns_head_rows(nested):
    d = _service().preview(nested, "source-data/b.csv", rows=5)
    assert d["path"] == "source-data/b.csv"
    assert d["head"] == [["S-1", "PARENT", "1.5"]]
    assert d["sampled"] is False
    assert _service().preview(nested, "a.csv", rows=0)["head"] == []


def test_source_bytes_unchanged_after_scan_preview_and_import(tmp_path):
    root = tmp_path / "Study"
    (root / "source-data").mkdir(parents=True)
    shutil.copyfile(FIXTURES / "two_analyte.csv", root / "two_analyte.csv")
    shutil.copyfile(FIXTURES / "two_analyte.csv", root / "source-data" / "copy.csv")
    ws = init_workspace(root, "0.0.0")
    before = _snapshot(root)
    service = _service()
    service.scan(root)
    service.preview(root, "two_analyte.csv")
    service.preview(root, "source-data/copy.csv", rows=200)
    data = service.preview(root, "two_analyte.csv")
    add_source(ws, "two_analyte.csv", data["size_bytes"], data["sha256"])
    after = _snapshot(root)
    meta = f"{WORKSPACE_META_DIR}/workspace.json"
    assert {k: v for k, v in after.items() if k != meta} == {
        k: v for k, v in before.items() if k != meta
    }
    assert data["sha256"] == hashlib.sha256((FIXTURES / "two_analyte.csv").read_bytes()).hexdigest()


def test_cache_follows_file_changes(tmp_path):
    importer = RecordingImporter()
    service = _service(importer)
    path = tmp_path / "pk.csv"
    path.write_bytes(CSV)
    assert service.preview(tmp_path, "pk.csv")["row_count"] == 1
    service.preview(tmp_path, "pk.csv")
    service.scan(tmp_path)
    assert len(importer.read_paths) == 1  # served from the cache
    path.write_bytes(CSV + b"S-2,METAB,2\n")
    assert service.preview(tmp_path, "pk.csv")["row_count"] == 2
    assert len(importer.read_paths) == 2


def test_preview_lists_both_fixture_analytes():
    d = _service().preview(FIXTURES, "two_analyte.csv")
    analytes = d["annotations"]["analytes"]
    (column,) = analytes["columns"]
    assert column["column"] == "PCTESTCD"
    values = {v["value"]: v["rows"] for v in column["values"]}
    assert {"PARENT", "METAB"} <= set(values)
    assert column["variants"] == [["METAB", "METAB "]]


def test_add_source_is_idempotent_and_versioned(tmp_path):
    (tmp_path / "pk.csv").write_bytes(CSV)
    ws = init_workspace(tmp_path, "0.0.0")
    sha = hashlib.sha256(CSV).hexdigest()
    ws, entry, created = add_source(ws, "pk.csv", len(CSV), sha)
    assert created and entry == {
        "id": f"pk-{sha[:8]}",
        "path": "pk.csv",
        "sha256": sha,
        "size_bytes": len(CSV),
    }
    ws, again, created = add_source(ws, "pk.csv", len(CSV), sha)
    assert not created and again == entry
    other = "0" * 64
    ws, changed, created = add_source(ws, "pk.csv", 10, other)
    assert created and changed["id"] == "pk-00000000"
    assert [s["id"] for s in ws.document["sources"]] == [entry["id"], changed["id"]]
    on_disk = (tmp_path / WORKSPACE_META_DIR / "workspace.json").read_text("utf-8")
    assert sha in on_disk and other in on_disk
