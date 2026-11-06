"""Offline CI (#44): scan, preview and import the two-analyte fixture through the app API.

    python scripts/offline/import_check.py <fixture.csv> <work dir>

Runs the launcher's FastAPI app in-process (the same app ``opncafyr launch`` serves) with
the session secret, opens a fresh study folder, and imports the fixture. Prints a short
JSON summary with counts only: no data values.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from opncafyr.core.app import create_app
from opncafyr.core.jobmanager import JobManager
from opncafyr.core.security import SESSION_HEADER, SecurityConfig
from opncafyr.core.workspace import RecentWorkspaces

PORT = 49_152


def main(fixture: Path, work: Path) -> int:
    study = (work / "Study").resolve()
    study.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(fixture, study / fixture.name)
    security = SecurityConfig(port=PORT)
    jobs = JobManager(poll_interval=0.05)
    app = create_app(
        security,
        jobs=jobs,
        picker=lambda prompt, start: study,
        recent=RecentWorkspaces(work / "state" / "recent.json"),
    )
    try:
        with TestClient(
            app,
            base_url=f"http://127.0.0.1:{PORT}",
            cookies={security.cookie_name: security.secret},
        ) as api:
            api.headers[SESSION_HEADER] = security.secret
            folder = api.post("/api/workspace/pick").json()["folder"]
            api.post("/api/workspace/init", json={"path": folder["path"]}).raise_for_status()
            files = api.get("/api/datasets").json()["files"]
            preview = api.post("/api/datasets/preview", json={"path": fixture.name})
            preview.raise_for_status()
            dataset = preview.json()["dataset"]
            imported = api.post("/api/datasets/import", json={"path": fixture.name})
            imported.raise_for_status()
            sources = json.loads((study / ".opncafyr" / "workspace.json").read_text("utf-8"))[
                "sources"
            ]
    finally:
        jobs.shutdown()
    summary = {
        "files_listed": len(files),
        "rows": dataset.get("row_count"),
        "columns": len(dataset.get("columns", [])),
        "sources": len(sources),
    }
    print(json.dumps(summary))
    if summary["sources"] != 1 or not summary["rows"]:
        print("preview or import did not complete", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__, file=sys.stderr)
        sys.exit(2)
    sys.exit(main(Path(sys.argv[1]), Path(sys.argv[2])))
