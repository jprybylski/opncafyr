"""Shared setup for the NCA workflow tests (#22-#29).

Cases, findings and the standard setup come from ``fixtures/two_analyte.expected.json``;
nothing here restates them (fixtures/README.md).
"""

from __future__ import annotations

import json
import os
import shutil
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from opncafyr.core.app import create_app
from opncafyr.core.jobmanager import JobManager
from opncafyr.core.runtime import RUNTIME_FILE_ENV, Runtime, load_runtime
from opncafyr.core.security import SESSION_HEADER
from opncafyr.core.workspace import RecentWorkspaces

FIXTURES = Path(__file__).parent / "fixtures"
EXPECTED = json.loads((FIXTURES / "two_analyte.expected.json").read_text("utf-8"))
SOURCE = "two_analyte.csv"


def standard_mapping() -> dict[str, Any]:
    """``standard_setup`` from the fixture as a recipe mapping block."""
    setup = EXPECTED["standard_setup"]
    units: dict[str, Any] = {}
    for key, role in (("time", "ACTUAL_TIME"), ("conc", "CONC"), ("dose", "DOSE")):
        text = setup["units"][key]
        units[role] = {"column": text[5:]} if text.startswith("from ") else {"value": text}
    flags = {
        "BLQ": {"table": setup["blq"]["flag_table"], "text_pattern": setup["blq"]["text_pattern"]},
        "MISSING": {"table": setup["missing"]["table"]},
        "EXCLUDED": {"table": setup["excluded"]["table"]},
    }
    assert setup["blq"]["flag_column"] == setup["mapping"]["BLQ"]
    assert setup["blq"]["text_column"] == setup["mapping"]["CONC"]
    assert setup["excluded"]["reason"] == setup["mapping"]["EXCLUSION_REASON"]
    return {"roles": dict(setup["mapping"]), "units": units, "flags": flags}


def integration_runtime() -> Runtime | None:
    path = os.environ.get(RUNTIME_FILE_ENV)
    if not path:
        return None
    rt = load_runtime(Path(path))
    return rt if rt.has("rscript") and rt.r_library is not None else None


def make_study(tmp_path: Path, files: tuple[str, ...] = (SOURCE,)) -> Path:
    root = tmp_path / "Study"
    (root / "source-data").mkdir(parents=True)
    for name in files:
        shutil.copyfile(FIXTURES / name, root / "source-data" / name)
    return root.resolve()


@contextmanager
def nca_client(security, tmp_path: Path, study: Path, runtime: Runtime | None = None, **kw):
    """A TestClient with the session set, the study opened and every fixture imported."""
    jobs = JobManager(runtime=runtime or Runtime(), poll_interval=0.05)
    app = create_app(
        security,
        jobs=jobs,
        picker=lambda prompt, start: study,
        recent=RecentWorkspaces(tmp_path / "state" / "recent.json"),
        runtime=runtime or Runtime(),
        **kw,
    )
    with TestClient(
        app,
        base_url=f"http://127.0.0.1:{security.port}",
        cookies={security.cookie_name: security.secret},
    ) as client:
        client.headers[SESSION_HEADER] = security.secret
        folder = client.post("/api/workspace/pick").json()["folder"]
        client.post("/api/workspace/init", json={"path": folder["path"]}).raise_for_status()
        for path in sorted((study / "source-data").glob("*.csv")):
            rel = f"source-data/{path.name}"
            client.post("/api/datasets/import", json={"path": rel}).raise_for_status()
        yield client
    jobs.shutdown()


def source_id(client: TestClient, name: str = SOURCE) -> str:
    for s in client.get("/api/nca/sources").json()["sources"]:
        if s["path"] == f"source-data/{name}":
            return s["id"]
    raise KeyError(name)


def new_recipe(client: TestClient, name: str = SOURCE, steps: list | None = None) -> dict:
    out = client.post("/api/nca/recipes", json={"source_id": source_id(client, name)})
    out.raise_for_status()
    doc = out.json()["recipe"]
    if steps:
        doc["steps"] = steps
        r = client.put(f"/api/nca/recipes/{doc['id']}", json={"recipe": doc})
        assert r.status_code == 200, r.text
        doc = r.json()["recipe"]
    return doc


def wait_job(client: TestClient, job_id: str, timeout: float = 120.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()["job"]
        if job["state"] in ("completed", "failed", "cancelled"):
            return job
        time.sleep(0.1)
    raise TimeoutError(job_id)


def apply(client: TestClient, recipe_id: str, kind: str = "apply") -> dict[str, Any]:
    """Run the recipe (``apply`` or ``preview``) and return its result."""
    started = client.post(f"/api/nca/recipes/{recipe_id}/{kind}")
    assert started.status_code == 200, started.text
    job_id = started.json()["job_id"]
    if job_id:
        job = wait_job(client, job_id)
        assert job["state"] == "completed", job["message"]
    result_kind = "preview" if kind == "preview" else "full"
    r = client.get(f"/api/nca/recipes/{recipe_id}/result", params={"kind": result_kind})
    assert r.status_code == 200, r.text
    return r.json()


FINDING_KEYS = (
    "check",
    "level",
    "analyte",
    "scope",
    "subject",
    "lines",
    "column",
    "value",
    "values",
    "kind",
)


def fixture_table(name: str = SOURCE) -> tuple[list[str], list[list[str]]]:
    """A fixture as the cleaning step would hand it on with no steps: every value as
    text, plus ``opncafyr_line`` (the physical line)."""
    import csv

    with (FIXTURES / name).open(newline="", encoding="utf-8") as fh:
        rows = list(csv.reader(fh))
    header = ["opncafyr_line", *rows[0]]
    return header, [[str(i), *r] for i, r in enumerate(rows[1:], start=2)]


def project(finding: dict[str, Any]) -> dict[str, Any]:
    """A finding with only the keys the fixture's expected findings use."""
    return {k: finding[k] for k in FINDING_KEYS if k in finding}


def case(case_id: str) -> dict[str, Any]:
    return next(c for c in EXPECTED["cases"] if c["id"] == case_id)


def resolving_steps() -> list[dict[str, Any]]:
    """Recipe steps that clear every blocking finding of the standard setup, built from the
    fixture's own findings and cases. Each is an explicit step with a reason: bad rows go
    to the rejected-row log; the reason-less exclusion and the no-dose profile are
    excluded (they stay in the data, marked)."""
    findings = EXPECTED["findings"][SOURCE]

    def lines(check, **match):
        return sorted(
            {
                n
                for f in findings
                if f["check"] == check and all(f.get(k) == v for k, v in match.items())
                for n in f["lines"]
            }
        )

    def remove(rows, reason):
        return {
            "op": "filter_rows",
            "args": {
                "where": {"column": "opncafyr_line", "in": [str(n) for n in rows]},
                "reason": reason,
            },
        }

    def exclude(rows, reason):
        return {
            "op": "exclude",
            "args": {
                "where": {"column": "opncafyr_line", "in": [str(n) for n in rows]},
                "reason": reason,
            },
        }

    steps = [
        remove(lines("conc_unclassified_blank"), "blank concentration, neither BLQ nor missing"),
        remove(lines("route_missing"), "route not recorded"),
        remove(lines("conc_negative"), "negative concentration"),
        remove(lines("time_not_numeric"), "actual time not recorded as a number"),
        remove(lines("conc_not_numeric"), "invalid extra sample (decimal comma, misspelt route)"),
        remove(lines("blq_and_missing"), "flagged both BLQ and missing"),
        remove(case("dose_varies")["lines"], "dose differs from the rest of the profile"),
    ]
    for f in findings:
        if f["check"] == "duplicate_key":
            steps.append(remove(f["lines"][1:], f"duplicate of line {f['lines'][0]}"))
    steps.append(exclude(lines("exclusion_no_reason"), "excluded at site; reason added here"))
    steps.append(exclude(lines("profile_no_dose"), "no dose record for this subject"))
    return steps


def resolving_mapping() -> dict[str, Any]:
    """The standard mapping, with the fixture's stray BLQ flag value confirmed as FALSE."""
    mapping = standard_mapping()
    for f in EXPECTED["findings"][SOURCE]:
        if f["check"] == "flag_value_unmapped":
            mapping["flags"]["BLQ"]["table"][f["value"]] = False
    return mapping
