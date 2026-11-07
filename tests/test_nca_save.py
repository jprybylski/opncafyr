"""Accepting an opnca-r save: launcher exclusions, in-app exclusions, mean settings (#28)."""

from __future__ import annotations

from pathlib import Path

from artifact_docs import SHA, run_manifest
from opncafyr.adapters.opnca import export
from opncafyr.core import runs as R
from opncafyr.workflows.nca import save

ROWS = (
    "USUBJID,AFRLT,NCA_EXCLUDED,NCA_EXCLUSION_REASON\n"
    "S-001,0,FALSE,\n"
    "S-001,1,TRUE,Sample collected outside the window\n"
    "S-002,0,FALSE,\n"
)
MEAN = {
    "file": "results/opnca-mean-profiles.csv",
    "time_basis": "nominal",
    "time_column": "NFRLT",
    "statistic": "arithmetic",
    "error_measure": "sd",
}


def running_run(root: Path) -> Path:
    (root / ".opncafyr").mkdir(parents=True)
    run_id, staging = R.stage_run(root)
    (staging / "input").mkdir()
    (staging / "input" / "analysis.csv").write_text(ROWS, "utf-8")
    (staging / "settings.json").write_text("{}", "utf-8")
    doc = run_manifest(run_id, state="preparing")
    doc["analysis_input"]["sha256"] = R.sha256_file(staging / "input" / "analysis.csv")
    R.write_manifest(staging, doc)
    folder = R.publish_run(staging, run_id)
    R.set_state(folder, "running")
    return folder


def status(record: dict) -> export.ExportStatus:
    artifacts = [
        {"path": p, "sha256": SHA, "kind": k, "media_type": "text/plain", "bytes": 1}
        for p, k in ((export.SCRIPT, "script"), (export.RECORD, "record"))
    ]
    return export.ExportStatus(
        state="complete",
        complete={"saved_at": "2026-11-07T17:00:00Z", "artifacts_sha256": SHA},
        artifacts=artifacts,
        record={"opnca_version": "0.2.9", "r_version": "R version 4.6.2", **record},
    )


def observations(*sources: str) -> dict:
    # opnca-r writes the data frame column-wise.
    return {
        "id": [f"S-00{i + 1}" for i in range(len(sources))],
        "time": [1.0] * len(sources),
        "source": list(sources),
    }


def test_in_app_exclusions_add_to_the_launchers(tmp_path):
    folder = running_run(tmp_path)
    record = {
        "mapping": {"excluded": "NCA_EXCLUDED"},
        "exclusions": {
            "mapped_column": "NCA_EXCLUDED",
            "observations": observations("mapping", "manual"),
        },
    }
    run = save.accept(folder, status(record))
    assert run["state"] == "reviewing"
    assert run["exclusions"]["launcher"]["rows"] == 1
    assert [o["source"] for o in run["exclusions"]["session"]] == ["manual"]
    assert any(w.startswith("session_exclusion_no_reason: 1 ") for w in run["warnings"])


def test_launcher_exclusions_alone_give_no_warning(tmp_path):
    folder = running_run(tmp_path)
    record = {"mapping": {"excluded": "NCA_EXCLUDED"}, "exclusions": {"observations": []}}
    run = save.accept(folder, status(record))
    assert run["state"] == "reviewing" and run["exclusions"]["session"] == []
    assert not any("session_exclusion" in w for w in run["warnings"])


def test_a_record_that_lost_the_launchers_column_fails(tmp_path):
    folder = running_run(tmp_path)
    record = {
        "mapping": {"excluded": "OPNCA_EXCLUDED"},
        "exclusions": {"observations": observations("manual")},
    }
    run = save.accept(folder, status(record))
    assert run["state"] == "failed"
    assert "replaced the launcher's 1 excluded rows" in run["failure"]["message"]


def test_the_mean_settings_are_kept_with_the_run(tmp_path):
    folder = running_run(tmp_path)
    record = {"mapping": {"excluded": "NCA_EXCLUDED"}, "mean_profiles": MEAN}
    run = save.accept(folder, status(record))
    assert run["nca_settings"]["mean_profiles"] == MEAN
