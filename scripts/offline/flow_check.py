"""Offline CI (#44, #51): the Gate 2 flow end to end through the launcher, with no network.

    python scripts/offline/flow_check.py <work dir> [--results <dir>]

Runs the launcher's FastAPI app in-process (the same ``create_app`` that ``opncafyr
launch`` serves, with its own supervisor and job manager) and drives it the way the home
page does: import ``tests/fixtures/two_analyte.csv`` -> clean -> map -> for each analyte a
one-analyte snapshot -> save in opnca-r (through ``/apps/opnca/``) -> replay -> complete
-> publish -> render the DOCX report -> build the PPTX deck. Nothing here calls a tool
directly: every R, Quarto, uv or deckifyr process is started by an adapter, which also
applies the offline settings (docs/offline.md). Start it with a clean environment so that
none of the caller's ``UV_*`` or ``R_*`` variables help.

The runtime is found as the launcher finds it (``core.runtime``): ``OPNCAFYR_RUNTIME_FILE``
in the offline job, the active install in the installer legs. The fixture's cleaning
steps, mapping and run choices come from ``tests/nca_helpers.py`` (the same ones the Gate 2
integration test uses), so ``tests/`` must sit next to ``scripts/`` as in the repository.

Without opnca-r (no release token) the flow goes as far as the two snapshots, then
publishes stand-in completed runs (``tests/publish_helpers.py``) so the report and deck
still run. The summary says so.

Writes ``flow.json`` (counts, IDs and hashes only, never data values) and appends each
step's duration to ``timings.jsonl`` in the results folder. Exits 1 on any failure.
"""

from __future__ import annotations

import argparse
import base64
import csv
import json
import sys
import time
import zipfile
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tests"))

import nca_helpers as N  # noqa: E402
import publish_helpers as P  # noqa: E402
from opncafyr.core import runs as R  # noqa: E402
from opncafyr.core.runtime import load_runtime  # noqa: E402
from opncafyr.core.security import SecurityConfig  # noqa: E402

PORT = 49_152
ANALYTES = ("PARENT", "METAB")
ANALYTE_COLUMN = N.EXPECTED["standard_setup"]["mapping"]["ANALYTE"]
# opnca-r's page draws its figures in the browser and sends them with the save; there is
# no browser here, so a stand-in PNG goes in its place. The tables are opnca-r's own.
FIGURE = "profiles-linear"
# The deck gets the tables too: opnca-r's tables have empty cells, which deckifyr could not
# build before 0.3.7 (jprybylski/deckifyr#96), so a deck table is part of this check.
DECK_ITEMS = ("summary", "listing", FIGURE)


class FlowError(RuntimeError):
    pass


def check(ok: bool, message: str) -> None:
    if not ok:
        raise FlowError(message)


class Steps:
    def __init__(self, results: Path) -> None:
        self.timings = results / "timings.jsonl"

    @contextmanager
    def step(self, name: str):
        print(f"== {name}", flush=True)
        start = time.monotonic()
        yield
        seconds = round(time.monotonic() - start, 1)
        print(f"   {name}: {seconds} s", flush=True)
        with self.timings.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"step": f"flow:{name}", "seconds": seconds}) + "\n")


def rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def ok_job(api, job_id: str, timeout: float, what: str) -> None:
    job = N.wait_job(api, job_id, timeout=timeout)
    check(job["state"] == "completed", f"{what}: {job['state']}: {job.get('message')}")


def opnca_installed(runtime) -> bool:
    lib = runtime.r_library
    return runtime.has("rscript") and lib is not None and (lib / "opnca").is_dir()


def run_folder(study: Path, run_id: str) -> Path:
    return study / ".opncafyr" / "runs" / run_id


def save_and_complete(api, study: Path, run_id: str) -> dict[str, Any]:
    """Save in opnca-r as the page's Save button does, replay, complete."""
    figure = base64.b64encode(P.png(FIGURE, size=64)).decode()
    N.save_in_opnca(
        api,
        study,
        run_id,
        save_extra={"figure_names": [FIGURE], "figure_formats": ["png"], "figure_data": [figure]},
    )
    status = N.save_status(api, run_id)
    check(status["run"]["state"] == "reviewing", f"{run_id}: not reviewing after the save")
    ok_job(api, api.app.state.nca.replays[run_id], 900, f"{run_id} replay")
    replay = N.save_status(api, run_id)["run"]["replay"]
    check(replay["status"] == "passed", f"{run_id}: replay {replay['status']}")
    done = api.post(f"/api/nca/runs/{run_id}/complete")
    check(done.status_code == 200, f"{run_id}: complete: {done.text}")
    return done.json()["run"]


def gate2(study: Path, runs: dict[str, dict[str, Any]], completed: bool) -> dict[str, Any]:
    """docs/plan.md Gate 2: separate runs, no cross-analyte rows (and, once saved, distinct
    scripts, records and outputs, every result profile from the run's own snapshot)."""
    ids = {r["run_id"] for r in runs.values()}
    check(len(ids) == len(runs), "two analytes share a run")
    keys = ("analysis_input", "script", "record", "settings") if completed else ("analysis_input",)
    for key in keys:
        hashes = {r[key]["sha256"] for r in runs.values()}
        check(len(hashes) == len(runs), f"two runs have the same {key}")
    out: dict[str, Any] = {}
    for analyte, run in runs.items():
        folder = run_folder(study, run["run_id"])
        snapshot = rows(folder / "input" / "analysis.csv")
        check(bool(snapshot), f"{analyte}: empty snapshot")
        check(
            {r[ANALYTE_COLUMN] for r in snapshot} == {analyte},
            f"{analyte}: the snapshot holds another analyte's rows",
        )
        entry = {"run_id": run["run_id"], "snapshot_rows": len(snapshot)}
        if completed:
            check(run["state"] == "completed", f"{analyte}: {run['state']}")
            check(R.verify_run(folder)["ok"] is True, f"{analyte}: sealed files changed")
            subjects = {r["USUBJID"] for r in snapshot}
            long = rows(folder / "results" / "opnca-parameters-long.csv")
            check(
                {r["profile_id"] for r in long} <= subjects,
                f"{analyte}: a result profile is not in the run's own snapshot",
            )
            entry |= {"result_rows": len(long), "replay": run["replay"]["status"]}
        out[analyte] = entry
    return out


def zip_output(study: Path, project: str, build: dict[str, Any], role: str, member: str):
    """Check one output of a build record (paths are relative to the build folder)."""
    out = next(o for o in build["outputs"] if o["role"] == role)
    path = study / project / "builds" / build["build_id"] / out["path"]
    sha256 = out["sha256"]
    check(path.is_file(), f"{out['path']} was not written")
    check(R.sha256_file(path) == sha256, f"{path.name} does not match its build record")
    with zipfile.ZipFile(path) as z:
        check(member in z.namelist(), f"{path.name} has no {member}")
    return {"build_id": build["build_id"], "sha256": sha256}


def no_errors(findings: list[dict[str, Any]], what: str) -> None:
    errors = [f.get("code") or f.get("message") for f in findings if f["severity"] == "error"]
    check(not errors, f"{what} check found errors: {errors}")


def flow(work: Path, results: Path) -> dict[str, Any]:
    steps = Steps(results)
    runtime = load_runtime()
    with_opnca = opnca_installed(runtime)
    summary: dict[str, Any] = {"runtime": str(runtime.source), "opnca": with_opnca}
    if not with_opnca:
        print("   opnca-r is not installed: the runs stop at the snapshot", flush=True)
    study = N.make_study(work)
    security = SecurityConfig(port=PORT)

    with ExitStack() as stack:
        stack.callback(P.writable, study)  # sealed runs are read-only
        with steps.step("import"):
            api = stack.enter_context(N.nca_client(security, work, study, runtime=runtime))
        stack.callback(api.app.state.supervisor.stop_all)
        check(len(api.get("/api/nca/sources").json()["sources"]) == 1, "import failed")

        with steps.step("clean"):
            recipe = N.new_recipe(api, steps=N.resolving_steps())
            cleaned = N.apply(api, recipe["id"])["summary"]
            summary["clean"] = {
                k: cleaned.get(k) for k in ("source_rows", "rows_out", "rows_rejected")
            }
            check(cleaned.get("rows_rejected", 0) > 0, "no row reached the rejected-row log")

        with steps.step("map"):
            r = api.put(
                f"/api/nca/recipes/{recipe['id']}/mapping",
                json={"mapping": N.resolving_mapping()},
            )
            check(r.status_code == 200, f"mapping: {r.text}")

        runs: dict[str, dict[str, Any]] = {}
        for analyte in ANALYTES:
            with steps.step(f"snapshot {analyte}"):
                runs[analyte] = N.start_run(api, recipe["id"], analyte)
            if with_opnca:
                with steps.step(f"opnca {analyte}"):
                    runs[analyte] = save_and_complete(api, study, runs[analyte]["run_id"])

        with steps.step("gate2"):
            summary["runs"] = gate2(study, runs, completed=with_opnca)

        with steps.step("publish"):
            if with_opnca:
                a, b = runs["PARENT"]["run_id"], runs["METAB"]["run_id"]
            else:
                a, _ = P.completed_run(study, "Stand-in A")
                b, _ = P.completed_run(study, "Stand-in B")
                summary["publish_from"] = "stand-in runs (no opnca-r)"
            items = P.picks(a, ["summary", "listing", FIGURE])
            items += P.picks(b, ["summary"])
            r = api.post("/api/nca/publications", json={"items": items})
            check(r.status_code == 201, f"publication: {r.text}")
            pub = r.json()["publication"]
            summary["publication"] = {"id": pub["publication_id"], "items": len(pub["items"])}

        with steps.step("report"):
            r = api.post("/api/nca/report", json={"publication_id": pub["publication_id"]})
            check(r.status_code == 200, f"report setup: {r.text}")
            ok_job(api, r.json()["job_id"], 600, "report setup")
            ok_job(api, api.post("/api/nca/report/check").json()["job_id"], 600, "report check")
            no_errors(api.get("/api/nca/report").json()["check"]["findings"], "report")
            r = api.post("/api/nca/report/render", json={"status": "draft"})
            check(r.status_code == 200, f"report render: {r.text}")
            ok_job(api, r.json()["job_id"], 1200, "report render")
            build = api.get("/api/nca/report").json()["builds"][0]
            check(sorted(build["runs"]) == sorted([a, b]), "the report names other runs")
            summary["report"] = zip_output(study, "report", build, "draft", "word/document.xml")

        with steps.step("deck"):
            r = api.post(
                "/api/nca/publications",
                json={"items": P.picks(a, DECK_ITEMS) + P.picks(b, DECK_ITEMS)},
            )
            check(r.status_code == 201, f"deck publication: {r.text}")
            deck_pub = r.json()["publication"]["publication_id"]
            r = api.post("/api/nca/deck", json={"publication_id": deck_pub})
            check(r.status_code == 200, f"deck setup: {r.text}")
            ok_job(api, r.json()["job_id"], 600, "deck setup")
            ok_job(api, api.post("/api/nca/deck/check").json()["job_id"], 600, "deck check")
            no_errors(api.get("/api/nca/deck").json()["check"]["findings"], "deck")
            r = api.post("/api/nca/deck/build", json={"previews": False})
            check(r.status_code == 200, f"deck build: {r.text}")
            ok_job(api, r.json()["job_id"], 1200, "deck build")
            build = api.get("/api/nca/deck").json()["builds"][0]
            check(sorted(build["runs"]) == sorted([a, b]), "the deck names other runs")
            summary["deck"] = zip_output(study, "deck", build, "pptx", "ppt/presentation.xml")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("work", type=Path, help="an empty folder for the study and its state")
    parser.add_argument("--results", type=Path, help="where flow.json goes (default: <work>)")
    args = parser.parse_args()
    work = args.work.resolve()
    work.mkdir(parents=True, exist_ok=True)
    results = (args.results or work).resolve()
    results.mkdir(parents=True, exist_ok=True)
    try:
        summary = flow(work, results)
    except (FlowError, AssertionError, TimeoutError) as exc:
        print(f"flow failed: {exc}", file=sys.stderr)
        (results / "flow.json").write_text(json.dumps({"error": str(exc)}, indent=2), "utf-8")
        return 1
    (results / "flow.json").write_text(json.dumps(summary, indent=2), "utf-8")
    print(json.dumps(summary, indent=2))
    print("flow complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
