"""reportifyr sidecar checks (#30)."""

import json
from pathlib import Path

import pytest

from opncafyr.core.manifest import validate
from opncafyr.workflows.nca.publish import sidecars as S
from opncafyr.workflows.nca.publish.api import bundled_footnotes

PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 32


def sidecar(rel: str, data: bytes, **object_meta) -> dict:
    meta = {
        "author": "tester",
        "path": rel,
        "creation_time": "2026-11-07 10:00:00",
        "file_type": rel.rpartition(".")[2],
        "meta_type": "NA",
        "hash": _b3(data),
        "table1": False,
        "footnotes": {"equations": [], "notes": ["A note."], "abbreviations": ["PK"]},
    }
    meta.update(object_meta)
    return {
        "system_meta": {"platform": "test", "software": {}},
        "source_meta": {"type": "script", "path": "analysis.R", "latest_time": "x"},
        "object_meta": meta,
        "addl_meta": [],
    }


def _b3(data: bytes) -> str:
    import blake3

    return blake3.blake3(data).hexdigest()


def publish(root: Path, rel: str, data: bytes, doc: dict | None | str = "default") -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    side = path.with_name(S.sidecar_name(path.name))
    if doc == "default":
        doc = sidecar(rel, data)
    if isinstance(doc, dict):
        side.write_text(json.dumps(doc), "utf-8")
    return side


@pytest.fixture
def footnotes():
    return bundled_footnotes()


def checks(root, footnotes, files=None):
    return [(f.check, f.path) for f in S.check_outputs(root, footnotes, files)[1]]


def test_blake3_matches_the_reference_vector(tmp_path):
    p = tmp_path / "abc"
    p.write_bytes(b"abc")
    assert S.blake3_file(p) == "6437b3ac38465133ffb63b75273a8db548c558465d79db03fd359c6cd5bd9d85"


def test_names_follow_reportifyr():
    assert S.sidecar_name("conc-time.png") == "conc-time_png_metadata.json"
    assert S.sidecar_name("a.b.csv") == "a.b_csv_metadata.json"
    assert S.is_figure("x.SVG") and not S.is_figure("x.csv") and not S.is_figure("x.rds")
    with pytest.raises(ValueError):
        S.sidecar_name("noext")


def test_bundled_footnotes_define_what_the_curator_uses(footnotes):
    assert {"PK", "NCA"} <= footnotes.abbreviations
    assert "conc-time-trajectories" in footnotes.figure_types


def test_valid_sidecars_pass(tmp_path, footnotes):
    publish(tmp_path, "OUTPUTS/figures/a.png", PNG)
    publish(tmp_path, "OUTPUTS/tables/b.csv", b"x,y\n1,2\n")
    files, findings = S.check_outputs(tmp_path, footnotes)
    assert files == ["OUTPUTS/figures/a.png", "OUTPUTS/tables/b.csv"]
    assert findings == []
    validate(S.preflight(findings), "preflight-v1")
    validate(sidecar("OUTPUTS/figures/a.png", PNG), "reportifyr-sidecar-v1")


def test_missing_sidecar(tmp_path, footnotes):
    publish(tmp_path, "OUTPUTS/figures/a.png", PNG, doc=None)
    assert checks(tmp_path, footnotes) == [("missing-sidecar", "OUTPUTS/figures/a.png")]


def test_missing_artifact_when_listed(tmp_path, footnotes):
    assert checks(tmp_path, footnotes, ["OUTPUTS/tables/gone.csv"]) == [
        ("missing-artifact", "OUTPUTS/tables/gone.csv")
    ]
    assert checks(tmp_path, footnotes, ["../outside.csv"])[0][0] == "missing-artifact"


@pytest.mark.parametrize("text", ["{not json", "[]", '{"object_meta": {}}', "﻿{}"])
def test_malformed_sidecar(tmp_path, footnotes, text):
    side = publish(tmp_path, "OUTPUTS/tables/b.csv", b"1\n", doc=None)
    side.write_text(text, "utf-8")
    assert checks(tmp_path, footnotes) == [
        ("invalid-sidecar", "OUTPUTS/tables/b_csv_metadata.json")
    ]


def test_wrong_hash(tmp_path, footnotes):
    publish(tmp_path, "OUTPUTS/tables/b.csv", b"1\n")
    (tmp_path / "OUTPUTS/tables/b.csv").write_bytes(b"2\n")
    assert checks(tmp_path, footnotes) == [("hash-mismatch", "OUTPUTS/tables/b_csv_metadata.json")]


def test_wrong_path_and_type(tmp_path, footnotes):
    doc = sidecar("OUTPUTS/tables/other.csv", b"1\n", file_type="txt")
    publish(tmp_path, "OUTPUTS/tables/b.csv", b"1\n", doc)
    assert [c for c, _ in checks(tmp_path, footnotes)] == ["path-mismatch", "file-type"]


def test_unknown_abbreviation(tmp_path, footnotes):
    footnotes_doc = {"equations": [], "notes": [], "abbreviations": ["PK", "NOT-A-KEY"]}
    publish(
        tmp_path,
        "OUTPUTS/tables/b.csv",
        b"1\n",
        sidecar("OUTPUTS/tables/b.csv", b"1\n", footnotes=footnotes_doc),
    )
    found = S.check_outputs(tmp_path, footnotes)[1]
    assert [f.check for f in found] == ["unknown-abbreviation"]
    assert "NOT-A-KEY" in found[0].message


def test_meta_type_must_match_the_file_kind(tmp_path, footnotes):
    # A figure footnote type on a table is unknown for tables.
    rel = "OUTPUTS/tables/b.csv"
    publish(tmp_path, rel, b"1\n", sidecar(rel, b"1\n", meta_type="conc-time-trajectories"))
    rel2 = "OUTPUTS/figures/a.png"
    publish(tmp_path, rel2, PNG, sidecar(rel2, PNG, meta_type="conc-time-trajectories"))
    assert checks(tmp_path, footnotes) == [
        ("unknown-meta-type", "OUTPUTS/tables/b_csv_metadata.json")
    ]


def test_orphan_sidecars_and_temp_files(tmp_path, footnotes):
    side = publish(tmp_path, "OUTPUTS/tables/b.csv", b"1\n")
    (tmp_path / "OUTPUTS/tables/b.csv").unlink()
    (tmp_path / "OUTPUTS/tables/.c.csv.tmp").write_bytes(b"partial")
    assert checks(tmp_path, footnotes) == [
        ("orphan-sidecar", side.relative_to(tmp_path).as_posix())
    ]


def test_links_are_refused(tmp_path, footnotes):
    real = tmp_path / "real.csv"
    real.write_bytes(b"1\n")
    (tmp_path / "OUTPUTS/tables").mkdir(parents=True)
    (tmp_path / "OUTPUTS/tables/b.csv").symlink_to(real)
    assert checks(tmp_path, footnotes, ["OUTPUTS/tables/b.csv"]) == [
        ("missing-artifact", "OUTPUTS/tables/b.csv")
    ]


def test_bad_footnotes_file(tmp_path):
    p = tmp_path / "f.yaml"
    for text in ("[1, 2]", "abbreviations: [a]", ":\n  - :"):
        p.write_text(text, "utf-8")
        with pytest.raises(S.SidecarError):
            S.load_footnotes(p)


def test_outputs_check_route(tmp_path, client):
    from opncafyr.core.workspace import init_workspace

    client.app.state.workspace = init_workspace(tmp_path, "0.0.1")
    publish(tmp_path, "OUTPUTS/figures/a.png", PNG)
    publish(tmp_path, "OUTPUTS/tables/b.csv", b"1\n", doc=None)
    out = client.get("/api/nca/outputs/check").json()
    assert out["files"] == ["OUTPUTS/figures/a.png", "OUTPUTS/tables/b.csv"]
    assert out["blocking"] is True
    assert [f["check"] for f in out["report"]["findings"]] == ["missing-sidecar"]


def test_outputs_check_needs_a_workspace(client):
    assert client.get("/api/nca/outputs/check").status_code == 409
