"""Interaction benchmark (#67): what each click costs, in time and in file/process work.

    python scripts/bench/interact.py <work dir> [--label NAME] [--reps 10] [--heavy-reps 3]
                                     [--extra-runs 20] [--only GROUP ...] [--profile OP]

Serves the launcher's app (the same ``create_app`` as ``opncafyr launch``) on a loopback
port with uvicorn and drives it over HTTP the way the home page does, with the synthetic
``tests/fixtures/two_analyte.csv`` study: the polls every open page repeats, opening and
listing things, cleaning, checks, a run's snapshot, opnca-r through the proxy, save and
replay, publishing, the report and the deck, and version history. Each operation runs
``--reps`` times (``--heavy-reps`` for the slow ones): the first is "cold", the rest give
the median and p95. Polls are measured again after ``--extra-runs`` more completed runs
are added, to show how their cost grows with the study.

Besides time, every operation records the files it opened, directories it listed, stats
and processes it started (``tests/perf_helpers.py``): on Windows each of these is far
more expensive than in a Linux container, so a count is the thing to fix.

Also measured: direct starts of the pinned executables (cold and warm) and a small file
benchmark in the work folder, to separate host costs from app costs.

Writes ``bench.json`` and ``bench.md`` into ``--results`` (default ``<work>/results``):
operation names, durations, counts and coarse system facts only. No paths, file names,
subject values or data.

The runtime is found as the launcher finds it (``OPNCAFYR_RUNTIME_FILE`` or the active
install). Start it with the installed launcher's Python, with ``scripts/`` and ``tests/``
side by side as in the repository.
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import cProfile
import io
import json
import os
import platform
import pstats
import shutil
import socket
import statistics
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tests"))
sys.path.insert(0, str(REPO / "scripts" / "bench"))

import httpx  # noqa: E402
import sysfacts  # noqa: E402
import uvicorn  # noqa: E402

import nca_helpers as N  # noqa: E402
import perf_helpers as PH  # noqa: E402
import publish_helpers as P  # noqa: E402
from opncafyr.core.app import create_app  # noqa: E402
from opncafyr.core.jobmanager import JobManager  # noqa: E402
from opncafyr.core.lifecycle import Settings  # noqa: E402
from opncafyr.core.runtime import Runtime, load_runtime  # noqa: E402
from opncafyr.core.security import SESSION_HEADER, SecurityConfig  # noqa: E402
from opncafyr.core.workspace import RecentWorkspaces  # noqa: E402

FIGURE = "profiles-linear"


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# --- recording -------------------------------------------------------------------------


class Bench:
    def __init__(self, profile: str | None) -> None:
        self.ops: dict[str, dict[str, Any]] = {}
        self.profile_op = profile
        self.profile_text: str | None = None

    def measure(self, group: str, name: str, fn: Callable[[], Any], reps: int) -> Any:
        """Run ``fn`` ``reps`` times; keep each duration and the counts of the first."""
        samples: list[float] = []
        counts: list[dict[str, Any]] = []
        out = None
        for i in range(reps):
            prof = cProfile.Profile() if (name == self.profile_op and i == reps - 1) else None
            with PH.count_io() as c:
                start = time.perf_counter()
                if prof:
                    prof.enable()
                out = fn()
                if prof:
                    prof.disable()
                samples.append(time.perf_counter() - start)
            counts.append(c.as_dict())
            if prof:
                buf = io.StringIO()
                pstats.Stats(prof, stream=buf).sort_stats("cumulative").print_stats(40)
                self.profile_text = buf.getvalue()
        self.ops[name] = {
            "group": group,
            **summarize(samples),
            "io": counts[-1],
            "io_cold": counts[0],
        }
        print(f"   {name:<44} {fmt(self.ops[name])}", flush=True)
        return out


def summarize(samples: list[float]) -> dict[str, Any]:
    warm = samples[1:] or samples
    ordered = sorted(warm)
    p95 = ordered[min(len(ordered) - 1, round(0.95 * (len(ordered) - 1)))]
    return {
        "n": len(samples),
        "cold_ms": round(samples[0] * 1000, 1),
        "median_ms": round(statistics.median(warm) * 1000, 1),
        "p95_ms": round(p95 * 1000, 1),
    }


def fmt(op: dict[str, Any]) -> str:
    io_ = op["io"]
    return (
        f"cold {op['cold_ms']:>8} ms  median {op['median_ms']:>8} ms  p95 {op['p95_ms']:>8} ms"
        f"  open {io_['open']:>5} list {io_['listdir']:>4} stat {io_['stat']:>6}"
        f" resolve {io_.get('resolve', 0):>5}"
        f" spawn {io_['spawn']:>2}"
    )


# --- host baselines ----------------------------------------------------------------------


def host_baselines(bench: Bench, runtime: Runtime, work: Path, reps: int) -> None:
    print("== host: process starts", flush=True)
    env = dict(os.environ)
    lib = runtime.r_library
    probes: list[tuple[str, list[str], dict[str, str]]] = []
    if runtime.has("python"):
        probes.append(
            ("start python", [str(runtime.executable("python")), "-I", "-c", "pass"], env)
        )
    if runtime.has("rscript"):
        rs = str(runtime.executable("rscript"))
        probes.append(("start Rscript", [rs, "--vanilla", "-e", "invisible(1)"], env))
        if lib is not None and (lib / "opnca").is_dir():
            renv = {**env, "R_LIBS": str(lib), "R_LIBS_USER": str(lib)}
            code = "suppressMessages(library(opnca))"
            probes.append(("start Rscript + library(opnca)", [rs, "--vanilla", "-e", code], renv))
    if runtime.has("git"):
        probes.append(("start git --version", [str(runtime.executable("git")), "--version"], env))
    if runtime.has("quarto"):
        probes.append(
            ("start quarto --version", [str(runtime.executable("quarto")), "--version"], env)
        )
    for name, argv, penv in probes:

        def run(argv=argv, penv=penv):
            subprocess.run(argv, env=penv, capture_output=True, check=True)  # noqa: S603 - pinned runtime

        bench.measure("host", name, run, reps)

    print("== host: small files", flush=True)
    folder = work / "fsbench"
    shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir(parents=True)
    names = [folder / f"f{i:04d}.json" for i in range(500)]

    def create():
        for p in names:
            p.write_text('{"a": 1}', "utf-8")

    def read():
        for p in names:
            p.read_text("utf-8")

    def stat():
        for p in names:
            p.is_file()

    def listing():
        sorted(folder.iterdir())

    def resolve():
        for p in names:
            p.resolve()

    def remove():
        for p in names:
            p.unlink()

    for _ in range(max(1, reps // 3)):
        bench.measure("host", "fs: create 500 files", create, 1)
        bench.measure("host", "fs: read 500 files", read, 1)
        bench.measure("host", "fs: stat 500 files", stat, 1)
        bench.measure("host", "fs: list a 500-file folder", listing, 1)
        bench.measure("host", "fs: resolve 500 paths", resolve, 1)
        bench.measure("host", "fs: delete 500 files", remove, 1)
    shutil.rmtree(folder, ignore_errors=True)


# --- the app --------------------------------------------------------------------------


@contextlib.contextmanager
def serve(work: Path, study: Path, runtime: Runtime):
    port = free_port()
    security = SecurityConfig(port=port)
    jobs = JobManager(runtime=runtime)
    app = create_app(
        security,
        jobs=jobs,
        picker=lambda prompt, start: study,
        recent=RecentWorkspaces(work / "state" / "recent.json"),
        settings=Settings(work / "state" / "settings.json"),
        tutorial_progress=work / "state" / "tutorials.json",
        open_tokens=work / "state" / "open",
        runtime=runtime,
    )
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="on")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"
    client = httpx.Client(
        base_url=base,
        cookies={security.cookie_name: security.secret},
        headers={SESSION_HEADER: security.secret},
        timeout=600,
    )
    deadline = time.monotonic() + 60
    while True:
        try:
            if client.get("/health").status_code == 200:
                break
        except httpx.TransportError:
            pass
        if time.monotonic() > deadline:
            raise RuntimeError("the app did not start")
        time.sleep(0.05)
    client.app = app  # the helpers read app.state (replay job IDs)
    try:
        yield client
    finally:
        client.close()
        server.should_exit = True
        thread.join(timeout=60)


def same_origin(client) -> dict[str, str]:
    return {"origin": str(client.base_url).rstrip("/"), "sec-fetch-site": "same-origin"}


def ok(r: httpx.Response, what: str) -> httpx.Response:
    if r.status_code >= 400:
        raise RuntimeError(f"{what}: {r.status_code} {r.text[:300]}")
    return r


def wait_job(client, job_id: str, timeout: float = 1200) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()["job"]
        if job["state"] in ("completed", "failed", "cancelled"):
            if job["state"] != "completed":
                raise RuntimeError(f"job {job.get('kind', '')}: {job['state']}")
            return
        time.sleep(0.05)
    raise TimeoutError(job_id)


POLLS = (
    # What the open pages ask for again and again (web/static/js/*.js).
    ("GET", "/api/jobs"),
    ("GET", "/api/tools"),
    ("GET", "/api/apps"),
    ("GET", "/api/lifecycle"),
    ("POST", "/api/session/heartbeat"),
    ("GET", "/api/workspace/artifacts"),
)
VIEWS = (
    # What opening a view loads.
    ("GET", "/"),
    ("GET", "/static/app.css"),
    ("GET", "/api/workspace"),
    ("GET", "/api/workflows"),
    ("GET", "/api/guidance"),
    ("GET", "/api/tutorials"),
    ("GET", "/api/datasets"),
    ("GET", "/api/nca/sources"),
    ("GET", "/api/nca/recipes"),
    ("GET", "/api/nca/runs"),
    ("GET", "/api/nca/publish/runs"),
    ("GET", "/api/nca/publications"),
    ("GET", "/api/nca/outputs/check"),
    ("GET", "/api/nca/profiles"),
    ("GET", "/api/nca/report"),
    ("GET", "/api/nca/deck"),
    ("GET", "/api/nca/history"),
    ("GET", "/api/standards"),
)


def request_ops(bench: Bench, client, group: str, routes, reps: int, suffix: str = "") -> None:
    for method, path in routes:

        def call(method=method, path=path):
            r = client.request(method, path)
            if r.status_code >= 500:
                raise RuntimeError(f"{method} {path}: {r.status_code}")

        bench.measure(group, f"{method} {path}{suffix}", call, reps)


def has_opnca(runtime: Runtime) -> bool:
    lib = runtime.r_library
    return runtime.has("rscript") and lib is not None and (lib / "opnca").is_dir()


def job(client, method: str, path: str, what: str, **kw) -> None:
    """Start a job the way a page button does and wait for it to complete."""
    r = ok(client.request(method, path, **kw), what).json()
    if r.get("job_id"):  # none when the same result is already there
        wait_job(client, r["job_id"])


class Flow:
    """The user's path through the app, measured one operation at a time."""

    def __init__(self, bench: Bench, client, study: Path, runtime: Runtime, args) -> None:
        self.bench = bench
        self.c = client
        self.study = study
        self.rt = runtime
        self.reps = args.reps
        self.heavy = args.heavy_reps
        self.extra_runs = args.extra_runs
        self.only = set(args.only or [])
        self.opnca = has_opnca(runtime)
        self.recipe = ""
        self.runs: list[str] = []
        self.completed: list[str] = []
        self.git_api: str | None = None

    def want(self, group: str) -> bool:
        return not self.only or group in self.only

    def m(self, group: str, name: str, fn: Callable[[], Any], reps: int | None = None) -> Any:
        return self.bench.measure(group, name, fn, reps or self.reps)

    def open(self) -> None:
        print("== app: open", flush=True)
        c = self.c
        self.m("http", "GET /health (loopback floor)", lambda: c.get("/health"))
        path = ok(c.post("/api/workspace/pick"), "pick").json()["folder"]["path"]
        ok(c.post("/api/workspace/init", json={"path": path}), "init")
        self.m(
            "open",
            "POST /api/workspace/open",
            lambda: ok(c.post("/api/workspace/open", json={"path": path}), "open"),
        )
        rel = {"path": f"source-data/{N.SOURCE}"}
        self.m(
            "open",
            "POST /api/datasets/preview",
            lambda: ok(c.post("/api/datasets/preview", json=rel), "preview"),
        )
        ok(c.post("/api/datasets/import", json=rel), "import")

    def pages(self, suffix: str = "") -> None:
        if self.want("poll"):
            print(f"== app: polls{suffix}", flush=True)
            request_ops(self.bench, self.c, "poll", POLLS, self.reps, suffix)
        if self.want("view"):
            print(f"== app: views{suffix}", flush=True)
            request_ops(self.bench, self.c, "view", VIEWS, self.reps, suffix)

    def clean(self) -> None:
        print("== app: clean, map, check", flush=True)
        c = self.c
        rid = self.recipe = N.new_recipe(c, steps=N.resolving_steps())["id"]
        if not self.rt.has("rscript"):
            print("   no pinned Rscript: cleaning, checks and runs are skipped", flush=True)
            return
        self.m(
            "clean",
            "preview cleaning (job)",
            lambda: job(c, "POST", f"/api/nca/recipes/{rid}/preview", "preview"),
            self.heavy,
        )
        self.m(
            "clean",
            "apply cleaning (job)",
            lambda: job(c, "POST", f"/api/nca/recipes/{rid}/apply", "apply"),
            self.heavy,
        )
        mapping = {"mapping": N.resolving_mapping()}
        self.m(
            "clean",
            "PUT recipe mapping",
            lambda: ok(c.put(f"/api/nca/recipes/{rid}/mapping", json=mapping), "mapping"),
        )
        sel = {"analyte_value": "PARENT", "specimen_value": "PLASMA"}
        self.m(
            "clean",
            "GET recipe analytes",
            lambda: ok(c.get(f"/api/nca/recipes/{rid}/analytes"), "analytes"),
        )
        self.m(
            "clean",
            "POST recipe checks",
            lambda: ok(c.post(f"/api/nca/recipes/{rid}/checks", json=sel), "checks"),
        )
        self.m(
            "clean",
            "format check (job)",
            lambda: job(c, "POST", f"/api/nca/recipes/{rid}/format-check", "format", json=sel),
            self.heavy,
        )
        self.m(
            "run",
            "start a run (snapshot)",
            lambda: self.runs.append(N.start_run(c, rid, "PARENT")["run_id"]),
            self.heavy,
        )

    def nca(self) -> None:
        if not (self.opnca and self.runs and self.want("opnca")):
            return
        print("== app: opnca-r", flush=True)
        c = self.c
        run_id = self.runs[-1]
        self.m(
            "opnca",
            "open run in opnca-r (session)",
            lambda: ok(c.post(f"/api/nca/runs/{run_id}/session"), "session"),
            1,
        )
        api = "/apps/opnca/api/v1/"
        body = {**N.preset_of(self.study, run_id), "metrics": N.SAVE_METRICS}
        hdrs = same_origin(c)
        self.m("opnca", "GET opnca-r page (proxy)", lambda: ok(c.get("/apps/opnca/"), "page"))
        self.m("opnca", "GET opnca-r health (proxy)", lambda: ok(c.get(api + "health"), "health"))
        data: dict[str, Any] = {}

        def calculate():
            r = ok(c.post(api + "analysis/calculate", json=body, headers=hdrs), "calculate")
            data.update(r.json()["data"])

        self.m("opnca", "opnca-r calculate (proxy)", calculate)
        figure = base64.b64encode(P.png(FIGURE, size=64)).decode()
        save = {
            "figure_names": [FIGURE],
            "figure_formats": ["png"],
            "figure_data": [figure],
        }
        self.m(
            "opnca",
            "opnca-r save (proxy)",
            lambda: ok(
                c.post(
                    api + "export/save", headers=hdrs, json={"config_id": data["config_id"], **save}
                ),
                "save",
            ),
            1,
        )
        self.m(
            "opnca",
            "POST save-status",
            lambda: ok(c.post(f"/api/nca/runs/{run_id}/save-status"), "save-status"),
        )
        replay = c.app.state.nca.replays[run_id]
        self.m("opnca", "replay (job)", lambda: wait_job(c, replay), 1)
        self.m(
            "opnca",
            "POST run complete",
            lambda: ok(c.post(f"/api/nca/runs/{run_id}/complete"), "complete"),
            1,
        )
        self.completed.append(run_id)

    def publish(self) -> None:
        while len(self.completed) < 2:
            self.completed.append(P.completed_run(self.study, f"Stand-in {len(self.completed)}")[0])
        if not self.want("publish"):
            return
        print("== app: publish, report, deck", flush=True)
        c = self.c
        a, b = self.completed[-2], self.completed[-1]
        items = P.picks(a, ["summary", "listing", FIGURE]) + P.picks(b, ["summary"])
        pubs: list[str] = []
        self.m(
            "publish",
            "POST publication",
            lambda: pubs.append(
                ok(c.post("/api/nca/publications", json={"items": items}), "publication").json()[
                    "publication"
                ]["publication_id"]
            ),
        )
        if all(self.rt.has(t) for t in ("rscript", "quarto", "uv", "deckifyr_python")):
            job(c, "POST", "/api/nca/report", "report setup", json={"publication_id": pubs[-1]})
            self.m(
                "publish",
                "report check (job)",
                lambda: job(c, "POST", "/api/nca/report/check", "check"),
                self.heavy,
            )
            self.m(
                "publish",
                "report render (job)",
                lambda: job(
                    c, "POST", "/api/nca/report/render", "render", json={"status": "draft"}
                ),
                self.heavy,
            )
        if self.rt.has("deckifyr_python"):
            deck = P.picks(a, ("summary", "listing", FIGURE))
            r = ok(c.post("/api/nca/publications", json={"items": deck}), "deck publication")
            pid = r.json()["publication"]["publication_id"]
            job(c, "POST", "/api/nca/deck", "deck setup", json={"publication_id": pid})
            self.m(
                "publish",
                "deck check (job)",
                lambda: job(c, "POST", "/api/nca/deck/check", "check"),
                self.heavy,
            )
            self.m(
                "publish",
                "deck build (job)",
                lambda: job(c, "POST", "/api/nca/deck/build", "build", json={"previews": False}),
                self.heavy,
            )

    def history(self) -> None:
        if not (self.want("history") and self.rt.has("rscript") and self.rt.has("git")):
            return
        print("== app: version history", flush=True)
        c = self.c

        def open_history():
            ok(c.post("/api/nca/history", json={}), "history")
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline:
                apps = {a["id"]: a["state"] for a in c.get("/api/apps").json()["apps"]}
                if apps.get("gitneighbr") == "running":
                    return
                time.sleep(0.05)
            raise TimeoutError("gitneighbr did not start")

        self.m("history", "open version history (until ready)", open_history, 1)
        api = self.git_api = "/apps/gitneighbr/api/v1/"
        self.m(
            "history", "GET gitneighbr page (proxy)", lambda: ok(c.get("/apps/gitneighbr/"), "page")
        )
        status = c.get(api + "status").json()
        version = status.get("status_version") or (status.get("data") or {}).get("status_version")
        r = c.post(api + "init", json={"status_version": version}, headers=same_origin(c))
        if r.status_code >= 400 or r.json().get("ok") is False:
            print(f"   git init through gitneighbr failed ({r.status_code}): status not measured")
            self.git_api = None
            return
        # What the History page asks every 2 s while it is visible (gitneighbr App.svelte).
        self.m(
            "history", "gitneighbr status poll (proxy)", lambda: ok(c.get(api + "status"), "status")
        )

    def grow(self) -> None:
        if not self.extra_runs or not (self.want("poll") or self.want("view")):
            return
        for i in range(self.extra_runs):
            P.completed_run(self.study, f"Extra {i:03d}")
        suffix = f" @+{self.extra_runs} runs"
        self.pages(suffix)
        if self.git_api:
            self.m(
                "history",
                f"gitneighbr status poll (proxy){suffix}",
                lambda: ok(self.c.get(self.git_api + "status"), "status"),
            )


def app_ops(bench: Bench, runtime: Runtime, work: Path, args) -> dict[str, Any]:
    study = N.make_study(work)
    with contextlib.ExitStack() as stack:
        stack.callback(P.writable, study)  # sealed runs are read-only
        client = stack.enter_context(serve(work, study, runtime))
        stack.callback(client.app.state.supervisor.stop_all)
        flow = Flow(bench, client, study, runtime, args)
        flow.open()
        flow.pages()
        flow.clean()
        flow.nca()
        flow.publish()
        flow.history()
        flow.grow()
        return {"opnca": flow.opnca, "runs": len(flow.runs) + len(flow.completed)}


def write_md(doc: dict[str, Any], path: Path) -> None:
    lines = [
        f"# Interaction bench: {doc['label']}",
        "",
        "```",
        json.dumps(doc["system"], indent=2),
        "```",
        "",
        "| Group | Operation | Cold ms | Median ms | p95 ms | Opens | Lists | Stats | Resolves "
        "| Spawns |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name, op in doc["ops"].items():
        io_ = op["io"]
        lines.append(
            f"| {op['group']} | {name} | {op['cold_ms']} | {op['median_ms']} | {op['p95_ms']} "
            f"| {io_['open']} | {io_['listdir']} | {io_['stat']} | {io_.get('resolve', 0)} "
            f"| {io_['spawn']} |"
        )
    path.write_text("\n".join(lines) + "\n", "utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("work", type=Path, help="an empty folder for the study and its state")
    parser.add_argument("--results", type=Path)
    parser.add_argument("--label", default=platform.system().lower())
    parser.add_argument("--reps", type=int, default=10)
    parser.add_argument("--heavy-reps", type=int, default=3)
    parser.add_argument("--extra-runs", type=int, default=20)
    parser.add_argument(
        "--only",
        nargs="*",
        choices=["host", "poll", "view", "opnca", "publish", "history"],
        help="measure only these groups (cleaning and a run are always made)",
    )
    parser.add_argument("--profile", help="cProfile this operation's last repetition")
    args = parser.parse_args()
    work = args.work.resolve()
    if work.exists() and any(work.iterdir()):
        parser.error(f"{work} is not empty")
    work.mkdir(parents=True, exist_ok=True)
    results = (args.results or work / "results").resolve()
    results.mkdir(parents=True, exist_ok=True)

    runtime = load_runtime()
    bench = Bench(args.profile)
    started = time.monotonic()
    if not args.only or "host" in args.only:
        host_baselines(bench, runtime, work, args.reps)
    facts = app_ops(bench, runtime, work, args)
    doc = {
        "schema": "opncafyr-bench-1",
        "label": args.label,
        "system": {**sysfacts.collect(work), **facts},
        "reps": args.reps,
        "heavy_reps": args.heavy_reps,
        "extra_runs": args.extra_runs,
        "total_s": round(time.monotonic() - started, 1),
        "ops": bench.ops,
    }
    (results / "bench.json").write_text(json.dumps(doc, indent=2), "utf-8")
    write_md(doc, results / "bench.md")
    if bench.profile_text:
        (results / "profile.txt").write_text(bench.profile_text, "utf-8")
    print(f"bench: {results / 'bench.md'} ({doc['total_s']} s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
