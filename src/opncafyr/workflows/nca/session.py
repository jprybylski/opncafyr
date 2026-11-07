"""One opnca-r session per run (#27).

A session is started on a run's frozen snapshot with the preset written at snapshot time
(``input/opnca-mapping.json``), with the run folder as opnca-r's ``--output-dir`` and the
run id as its storage namespace, so nothing from an earlier run's session (mapping,
exclusions) carries over (opnca-r#30). Only one opnca-r child runs at a time: opening
another run stops the current one first (``core.supervisor``), and the launcher's own stop
stops it too. That, and the single-instance launcher, keep one session per output folder.

A run can be opened while it is ``preparing`` or ``running`` and opnca-r has not saved
into it; the first session moves it to ``running``.
"""

from __future__ import annotations

import csv
import json
import threading
from pathlib import Path
from typing import Any

from opncafyr.core import runs as runs_core
from opncafyr.workflows.nca.preset import check_preset
from opncafyr.workflows.nca.snapshot import INPUT, PRESET

TOOL = "opnca"
# What opnca-r's save writes; any of it means the run was saved (or half saved) already.
EXPORT_ENTRIES = ("analysis.R", "analysis-record.json", "artifacts.json", "results", "COMPLETE")


class SessionError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class Sessions:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.run_id: str | None = None

    def current(self, supervisor) -> dict[str, Any]:
        state = str(supervisor.state(TOOL)) if supervisor is not None else "not_started"
        running = state == "running"
        return {"run_id": self.run_id if running else None, "state": state}

    def start(self, supervisor, folder: Path) -> dict[str, Any]:
        manifest = runs_core.read_manifest(folder)
        if manifest["state"] not in ("preparing", "running"):
            raise SessionError(
                "run_not_open",
                f"Run {folder.name} is {manifest['state']}; it can't be opened in opnca-r "
                "again. Make a new run from it instead.",
            )
        if any((folder / name).exists() for name in EXPORT_ENTRIES):
            raise SessionError("run_saved", f"opnca-r has already saved into run {folder.name}.")
        data, preset_path = folder / INPUT, folder / PRESET
        if runs_core.sha256_file(data) != manifest["analysis_input"]["sha256"]:
            raise SessionError("input_changed", "The run's input no longer matches its hash.")
        with data.open(newline="", encoding="utf-8") as fh:
            header = next(csv.reader(fh))
        preset = json.loads(preset_path.read_text("utf-8"))
        check_preset(preset, header)
        settings = json.loads((folder / "settings.json").read_text("utf-8"))
        analyte = settings["analyte"]
        session = {
            "data_path": str(data),
            "output_dir": str(folder),
            "run_id": folder.name,
            "mapping_path": str(preset_path),
            "label": f"{analyte['label']} ({folder.name})",
            "context": {"analyte": analyte["value"], "run_id": folder.name},
        }
        with self._lock:
            self.run_id = None
            try:
                supervisor.start(TOOL, session)
            except Exception as exc:  # noqa: BLE001 - shown to the user, never logged with data
                raise SessionError("session_failed", f"opnca-r could not start: {exc}") from None
            self.run_id = folder.name
        runs_core.set_state(folder, "running")
        return self.current(supervisor)

    def stop(self, supervisor, run_id: str | None = None) -> dict[str, Any]:
        with self._lock:
            if run_id is None or run_id == self.run_id:
                supervisor.stop(TOOL)
                self.run_id = None
        return self.current(supervisor)
