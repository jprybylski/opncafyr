"""opnca-r's save into a run folder: what counts as complete (#28)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from opncafyr.adapters.opnca import export as E

SRC = Path(__file__).resolve().parents[1] / "src"
OWN = ("input", "settings.json", "manifest.json", "logs")
INPUT_TEXT = "USUBJID,AFRLT,NCA_CONC\nA,0,1\n"
INPUT_SHA = hashlib.sha256(INPUT_TEXT.encode()).hexdigest()


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def make_save(folder: Path, *, complete: bool = True) -> Path:
    """A run folder as opnca-r 0.2.9 leaves it after a save."""
    (folder / "input").mkdir(parents=True)
    (folder / "input" / "analysis.csv").write_text(INPUT_TEXT, "utf-8")
    (folder / "settings.json").write_text("{}", "utf-8")
    (folder / "manifest.json").write_text("{}", "utf-8")
    (folder / "logs").mkdir()
    files = {
        "analysis.R": b'df <- read.csv("input/analysis.csv", stringsAsFactors = FALSE)\n',
        "analysis-record.json": json.dumps(
            {"data": {"path": "input/analysis.csv", "sha256": INPUT_SHA}}
        ).encode(),
        "results/opnca-parameters-long.csv": b"profile_id,metric,value\nA,cmax,1\n",
        "results/figures/profiles-linear.png": b"\x89PNG fake",
    }
    for rel, data in files.items():
        path = folder / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    media = {".R": "text/x-r", ".json": "application/json", ".csv": "text/csv", ".png": "image/png"}
    listing = {
        "artifacts": [
            {
                "path": rel,
                "media_type": media[Path(rel).suffix],
                "bytes": len(data),
                "sha256": _sha(data),
            }
            for rel, data in files.items()
        ]
    }
    (folder / "artifacts.json").write_text(json.dumps(listing, indent=2), "utf-8")
    if complete:
        digest = _sha((folder / "artifacts.json").read_bytes())
        (folder / "COMPLETE").write_text(
            json.dumps({"saved_at": "2026-11-07T00:00:00Z", "artifacts_sha256": digest}), "utf-8"
        )
    return folder


def inspect(folder, alive=False):
    return E.inspect_export(folder, own=OWN, snapshot_sha256=INPUT_SHA, session_alive=alive)


def test_a_complete_save(tmp_path):
    status = inspect(make_save(tmp_path / "run"))
    assert status.state == "complete", status.problems
    kinds = {a["path"]: a["kind"] for a in status.artifacts}
    assert kinds == {
        "analysis.R": "script",
        "analysis-record.json": "record",
        "results/opnca-parameters-long.csv": "table",
        "results/figures/profiles-linear.png": "figure",
    }


def test_nothing_saved_yet(tmp_path):
    folder = tmp_path / "run"
    (folder / "input").mkdir(parents=True)
    assert inspect(folder).state == "none"


@pytest.mark.parametrize("alive", [True, False])
@pytest.mark.parametrize("marker", [".opnca-save.lock", ".staging-123-abcdef"])
def test_a_save_in_progress(tmp_path, marker, alive):
    folder = make_save(tmp_path / "run", complete=False)
    (folder / marker).mkdir()
    assert inspect(folder, alive).state == ("saving" if alive else "failed")


def _break(folder: Path, how: str) -> None:
    if how == "no_complete":
        (folder / "COMPLETE").unlink()
    elif how == "complete_hash":
        (folder / "COMPLETE").write_text('{"artifacts_sha256": "' + "0" * 64 + '"}', "utf-8")
    elif how == "result_changed":
        (folder / "results" / "opnca-parameters-long.csv").write_text("changed\n", "utf-8")
    elif how == "result_missing":
        (folder / "results" / "opnca-parameters-long.csv").unlink()
    elif how == "unlisted_result":
        (folder / "results" / "extra.csv").write_text("x\n", "utf-8")
    elif how == "stray_top_level":
        (folder / "notes.txt").write_text("x\n", "utf-8")
    elif how == "results_without_complete":
        (folder / "COMPLETE").unlink()
        (folder / "analysis.R").unlink()
    elif how == "other_input":
        record = {"data": {"path": "input/analysis.csv", "sha256": "1" * 64}}
        _rewrite(folder, "analysis-record.json", json.dumps(record).encode())
    elif how == "absolute_input":
        _rewrite(folder, "analysis.R", b'df <- read.csv("/tmp/your_data.csv")\n')
    elif how == "escaping_path":
        listing = json.loads((folder / "artifacts.json").read_text("utf-8"))
        listing["artifacts"].append(
            {"path": "../outside.csv", "bytes": 1, "sha256": "0" * 64, "media_type": "text/csv"}
        )
        (folder / "artifacts.json").write_text(json.dumps(listing), "utf-8")
        digest = _sha((folder / "artifacts.json").read_bytes())
        (folder / "COMPLETE").write_text(json.dumps({"artifacts_sha256": digest}), "utf-8")


def _rewrite(folder: Path, rel: str, data: bytes) -> None:
    """Change a file and keep artifacts.json and COMPLETE consistent with it."""
    (folder / rel).write_bytes(data)
    listing = json.loads((folder / "artifacts.json").read_text("utf-8"))
    for art in listing["artifacts"]:
        if art["path"] == rel:
            art["bytes"], art["sha256"] = len(data), _sha(data)
    (folder / "artifacts.json").write_text(json.dumps(listing), "utf-8")
    digest = _sha((folder / "artifacts.json").read_bytes())
    (folder / "COMPLETE").write_text(json.dumps({"artifacts_sha256": digest}), "utf-8")


@pytest.mark.parametrize(
    "how",
    [
        "no_complete",
        "complete_hash",
        "result_changed",
        "result_missing",
        "unlisted_result",
        "stray_top_level",
        "results_without_complete",
        "other_input",
        "absolute_input",
        "escaping_path",
    ],
)
def test_anything_short_of_complete_fails(tmp_path, how):
    folder = make_save(tmp_path / "run")
    _break(folder, how)
    status = inspect(folder)
    assert status.state == "failed" and status.problems


def test_launcher_temp_files_are_not_stray(tmp_path):
    folder = make_save(tmp_path / "run")
    (folder / ".manifest.json.abc123.tmp").write_text("{", "utf-8")
    assert inspect(folder).state == "complete"


def test_no_code_reads_browser_downloads():
    for path in SRC.rglob("*"):
        if path.suffix in (".py", ".js", ".R", ".html"):
            text = path.read_text("utf-8", errors="replace")
            assert "Downloads" not in text, path
            assert "download.folder" not in text.lower(), path
