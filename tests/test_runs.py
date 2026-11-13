"""Run bundles: manifest validation, atomic writes, states, sealing, integrity (#26)."""

from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest
from jsonschema import ValidationError

from artifact_docs import SHA, run_manifest
from opncafyr import cli
from opncafyr.core import runs as R
from opncafyr.core.manifest import validate

SPEC = Path(__file__).resolve().parents[1] / "docs" / "spec.md"
REQUIRED = [
    "schema_version",
    "run_id",
    "created_at",
    "state",
    "source",
    "recipe",
    "analysis_input",
    "analyte",
    "mapping",
    "units",
    "tools",
]


def _writable(root: Path) -> None:
    for path in [root, *root.rglob("*")]:
        if not path.is_symlink():
            path.chmod(path.stat().st_mode | stat.S_IWUSR)


@pytest.fixture
def workspace(tmp_path):
    root = tmp_path / "Study"
    (root / ".opncafyr").mkdir(parents=True)
    yield root
    _writable(root)


def _new_run(root: Path, state: str = "preparing") -> Path:
    run_id, staging = R.stage_run(root)
    (staging / "input").mkdir()
    (staging / "input" / "analysis.csv").write_text("A,B\n1,2\n", "utf-8")
    (staging / "settings.json").write_text("{}", "utf-8")
    doc = run_manifest(run_id, state=state)
    doc["analysis_input"]["sha256"] = R.sha256_file(staging / "input" / "analysis.csv")
    R.write_manifest(staging, doc)
    return R.publish_run(staging, run_id)


def _review(folder: Path) -> None:
    (folder / "analysis.R").write_text("x <- 1\n", "utf-8")
    R.set_state(folder, "running")
    R.set_state(
        folder,
        "reviewing",
        script={"path": "analysis.R", "sha256": R.sha256_file(folder / "analysis.R")},
        record={"path": "analysis.R", "sha256": SHA},
        export={"saved_at": "2026-11-03T14:30:00Z", "artifacts_sha256": SHA},
        artifacts=[],
        nca_settings={},
    )


REPLAY = {
    "status": "passed",
    "rtol": 1e-10,
    "path": "logs/replay.json",
    "sha256": SHA,
    "at": "2026-11-03T14:31:00Z",
}


# --- Schema --------------------------------------------------------------------------


def test_spec_example_validates():
    text = SPEC.read_text("utf-8")
    block = re.search(r"```json\n(\{\n  \"schema_version\".*?)\n```", text, re.S).group(1)
    block = block.replace("<digest>", SHA).replace("<version>", "0.0.1")
    block = block.replace("<pinned version>", "1.0")
    validate(json.loads(block), "run-manifest-v1")


@pytest.mark.parametrize("field", REQUIRED)
def test_a_missing_required_field_fails(field):
    doc = run_manifest(state="preparing")
    del doc[field]
    with pytest.raises(ValidationError):
        validate(doc, "run-manifest-v1")


@pytest.mark.parametrize(
    ("state", "field"),
    [
        ("reviewing", "script"),
        ("reviewing", "artifacts"),
        ("completed", "sealed"),
        ("completed", "replay"),
        ("failed", "failure"),
    ],
)
def test_later_states_need_their_fields(state, field):
    doc = run_manifest(state=state)
    del doc[field]
    with pytest.raises(ValidationError):
        validate(doc, "run-manifest-v1")


# --- Atomic writes -------------------------------------------------------------------


def test_a_failed_rename_leaves_the_old_manifest(workspace, monkeypatch):
    folder = _new_run(workspace)
    before = (folder / "manifest.json").read_bytes()

    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        R.set_state(folder, "running")
    assert (folder / "manifest.json").read_bytes() == before
    assert [p.name for p in folder.iterdir() if p.name.endswith(".tmp")] == []


WRITER = """
import sys, itertools
sys.path.insert(0, {tests!r})
from artifact_docs import run_manifest
from opncafyr.core.manifest import atomic_write_json
for i in itertools.count():
    doc = run_manifest(state="preparing", warnings=["x" * (i % 5000)])
    atomic_write_json({path!r}, doc, "run-manifest-v1")
"""


def _read_settled(path: Path) -> str:
    """Read ``path``, retrying while Windows refuses the open.

    Windows denies opening a file for a moment while ``os.replace`` swaps it in, or while a
    scanner holds the new file; that says nothing about its contents. Other platforms read
    on the first try.
    """
    deadline = time.monotonic() + 2
    while True:
        try:
            return path.read_text("utf-8")
        except PermissionError:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.01)


def test_a_killed_writer_never_leaves_a_partial_manifest(tmp_path):
    path = tmp_path / "manifest.json"
    code = WRITER.format(tests=str(Path(__file__).parent), path=str(path))
    for delay in (0.4, 0.55, 0.7, 0.85, 1.0):
        proc = subprocess.Popen([sys.executable, "-c", code])
        deadline = time.monotonic() + 30
        while not path.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        time.sleep(delay / 4)
        proc.kill()
        proc.wait()
        validate(json.loads(_read_settled(path)), "run-manifest-v1")


def test_publish_refuses_a_folder_without_a_valid_manifest(workspace):
    run_id, staging = R.stage_run(workspace)
    with pytest.raises(R.RunError):
        R.publish_run(staging, run_id)
    assert not (staging.parent / run_id).exists()


# --- States and sealing --------------------------------------------------------------


def test_states_follow_the_job_states(workspace):
    folder = _new_run(workspace)
    R.set_state(folder, "running")
    R.set_state(folder, "running")  # re-opening a session is not a change
    with pytest.raises(R.RunError):
        R.set_state(folder, "preparing")
    with pytest.raises(R.RunError):
        R.set_state(folder, "completed")  # not straight from running


def test_seal_lists_every_file_and_makes_the_run_read_only(workspace):
    folder = _new_run(workspace)
    _review(folder)
    doc = R.seal_run(folder, replay=REPLAY)
    assert doc["state"] == "completed"
    listed = {f["path"] for f in doc["sealed"]["files"]}
    assert listed == {"analysis.R", "input/analysis.csv", "settings.json"}
    for path in folder.rglob("*"):
        if path.is_file():
            assert not os.access(path, os.W_OK), path
    if os.name != "nt":
        assert not os.access(folder, os.W_OK)
    assert R.verify_run(folder)["ok"] is True
    with pytest.raises(R.RunSealedError):
        R.set_state(folder, "failed", failure={"reason": "x", "message": "x", "at": R.now()})
    with pytest.raises(R.RunSealedError):
        R.write_manifest(folder, doc)


def test_integrity_check_finds_changed_missing_and_added_files(workspace):
    folder = _new_run(workspace)
    _review(folder)
    R.seal_run(folder, replay=REPLAY)
    _writable(folder)
    (folder / "analysis.R").write_text("x <- 2\n", "utf-8")
    (folder / "settings.json").unlink()
    (folder / "results.csv").write_text("new\n", "utf-8")
    report = R.verify_run(folder)
    assert report["ok"] is False
    assert report["changed"] == ["analysis.R"]
    assert report["missing"] == ["settings.json"]
    assert report["added"] == ["results.csv"]


def test_unsealed_runs_check_what_the_manifest_names(workspace):
    folder = _new_run(workspace)
    assert R.verify_run(folder)["ok"] is True
    (folder / "input" / "analysis.csv").write_text("A,B\n9,9\n", "utf-8")
    assert R.verify_run(folder)["changed"] == ["input/analysis.csv"]


def test_cli_verify(workspace, capsys):
    good = _new_run(workspace)
    _review(good)
    R.seal_run(good, replay=REPLAY)
    assert cli.main(["runs", "verify", str(workspace)]) == 0
    assert "ok" in capsys.readouterr().out
    _writable(good)
    (good / "analysis.R").write_text("changed\n", "utf-8")
    assert cli.main(["runs", "verify", str(workspace), "--run", good.name]) == 1
    out = capsys.readouterr().out
    assert "DRIFT" in out and "changed  analysis.R" in out
    assert cli.main(["runs", "verify", str(workspace), "--run", "nope"]) == 2


def test_run_ids_are_checked(workspace):
    for bad in ("../x", "20261103T142512Z-7c4e/..", "abc"):
        with pytest.raises(R.RunError):
            R.run_dir(workspace, bad)
