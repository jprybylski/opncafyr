"""Regenerate the user guide's screenshots (docs/user-guide/images/, #40).

The images are produced, never hand-edited: rerun this script after a UI change and
commit the new PNGs. It starts a real controller in this process on a loopback port, with
a temporary app root and a temporary study folder ``Study PK-101`` holding
``source-data/two_analyte.csv`` (the committed test fixture). It drives the two-analyte
flow through the HTTP API the way the integration tests do (import, clean, map, one run
per analyte in opnca-r, replay, complete, publish, report and deck setup) and uses a
headless Chromium only to navigate the pages and capture them.

It needs a full runtime record (Rscript with opnca-r in the app-private R library,
Quarto, uv and deckifyr), as the integration tests do::

    uv run --group integration playwright install chromium   # once
    uv run --group integration python scripts/guide_screenshots.py --runtime runtime.json
    uv run --group integration python scripts/guide_screenshots.py --runtime runtime.json \\
        --only 09-report 10-deck
    uv run python scripts/guide_screenshots.py --check

``--only`` still runs the whole flow (later pages need the earlier steps) but writes only
the named images. ``--check`` lists the images that ``docs/user-guide/*.md`` references
and exits 1 if any of them is missing. A state that can't be reached with the given
runtime is reported and its image skipped; the script then exits 1.

``--serve`` runs the app instead, with an empty study folder (``TA-walkthrough``) that the
folder picker always chooses, until interrupted: for following the guide's walkthrough by
hand. It prints the folder and a file of one-time links to open the app.

Nothing leaves the machine: the controller binds to 127.0.0.1 and the temporary folders
are removed at the end.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import socket
import sys
import tempfile
import threading
import time
import traceback
from collections.abc import Callable
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TESTS = REPO / "tests"
GUIDE = REPO / "docs" / "user-guide"
DEFAULT_OUT = GUIDE / "images"
FIXTURE = TESTS / "fixtures" / "two_analyte.csv"
STUDY_NAME = "Study PK-101"
SOURCE_PATH = "source-data/two_analyte.csv"

NAMES = (
    "01-home",
    "02-data-preview",
    "03-cleaning",
    "04-mapping",
    "05-checks",
    "06-opnca",
    "07-runs",
    "08-publish",
    "09-report",
    "10-deck",
    "11-support",
    "12-history",
    "13-history-setup",
    "14-history-changes",
    "15-history-save",
    "16-study-profile",
    "17-tutorial",
)
VIEWPORT = {"width": 1280, "height": 800}
MAX_BYTES = 400 * 1024
# Cards taller than this are cropped to their top part.
MAX_CROP_HEIGHT = 1100
REFERENCE = re.compile(r"\]\(images/([A-Za-z0-9_.-]+)\.png\)")

# A made-up study profile for the practice file (16-study-profile): one run per analyte,
# published together. Written into the temporary study only; it is not a shipped example.
PRACTICE_PROFILE_NAME = "two-analyte-practice.yaml"
PRACTICE_PROFILE = """\
schema_version: "1.0"
id: two-analyte-practice
version: "1"
title: Two-analyte practice study
description: A made-up profile for the practice file. One run per analyte, published together.
steps:
  - id: ready
    type: checklist
    title: Before you start
    items:
      - id: plan
        text: The analysis plan is at hand.
      - id: imported
        text: two_analyte.csv is imported on the Data page.
  - id: map
    type: action
    title: Clean and map the data
    target: nca.mapping.card
    hint_condition: nca.mapping.ready
    text: Apply the recipe to all rows, then map the columns and save the mapping.
  - id: run-parent
    type: run
    title: PARENT run
    slot: parent
    suggested_label: Parent
  - id: run-metab
    type: run
    title: METAB run
    slot: metab
    suggested_label: Metabolite
  - id: review
    type: checkpoint
    title: Review both runs
    text: Check that both runs use the same units, BLQ choices and exclusion rules.
  - id: publication
    type: publication
    title: Publication with both analytes
    slots: [parent, metab]
"""


# --- --check ------------------------------------------------------------------------


def referenced_images() -> list[str]:
    names: set[str] = set()
    for md in sorted(GUIDE.glob("*.md")):
        names.update(REFERENCE.findall(md.read_text("utf-8")))
    return sorted(names)


def check(out: Path) -> int:
    missing = []
    for name in referenced_images():
        present = (out / f"{name}.png").is_file()
        print(f"{'ok     ' if present else 'MISSING'} images/{name}.png")
        if not present:
            missing.append(name)
    if missing:
        print(f"{len(missing)} referenced image(s) missing; rerun this script.", file=sys.stderr)
        return 1
    return 0


# --- the controller -----------------------------------------------------------------


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Controller:
    """The launcher's controller in this process, as tests/ui/conftest.py starts it, but
    with the real runtime, the product tools, a real supervisor and a real job manager."""

    def __init__(self, tmp: Path, runtime_file: Path, study_name: str = STUDY_NAME) -> None:
        import httpx
        import uvicorn

        from opncafyr.core.app import build_supervisor, create_app
        from opncafyr.core.jobmanager import JobManager
        from opncafyr.core.lifecycle import LifecycleConfig, Settings
        from opncafyr.core.registry import load_descriptors
        from opncafyr.core.runtime import load_runtime
        from opncafyr.core.security import SESSION_HEADER, SecurityConfig
        from opncafyr.core.workspace import RecentWorkspaces
        from tool_sets import DEV_ONLY

        self.tmp = tmp
        self.study = tmp / study_name
        if study_name == STUDY_NAME:
            (self.study / "source-data").mkdir(parents=True)
            shutil.copyfile(FIXTURE, self.study / SOURCE_PATH)
        else:  # --serve: an empty folder, as a new user starts
            self.study.mkdir(parents=True)
        self.runtime = load_runtime(runtime_file)
        tools = {k: v for k, v in load_descriptors().items() if k not in DEV_ONLY}
        self.supervisor = build_supervisor(tools, self.runtime)
        self.jobs = JobManager(runtime=self.runtime)
        self.tokens = tmp / "state" / "open-tokens"
        port = _free_port()
        security = SecurityConfig(port=port)
        study = self.study
        self.app = create_app(
            security,
            request_shutdown=lambda: None,
            supervisor=self.supervisor,
            jobs=self.jobs,
            picker=lambda prompt, start: study,
            recent=RecentWorkspaces(tmp / "state" / "recent.json"),
            settings=Settings(tmp / "state" / "settings.json"),
            lifecycle=LifecycleConfig(),
            tools=tools,
            runtime=self.runtime,
            open_tokens=self.tokens,
            tutorial_progress=tmp / "state" / "tutorial-progress.json",
        )
        self.server = uvicorn.Server(
            uvicorn.Config(
                self.app, host="127.0.0.1", port=port, log_level="warning", lifespan="on"
            )
        )
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.thread.start()
        deadline = time.monotonic() + 30
        while not self.server.started:
            if time.monotonic() > deadline or not self.thread.is_alive():
                raise RuntimeError("the controller did not start")
            time.sleep(0.05)
        self.base = f"http://127.0.0.1:{port}"
        self.client = httpx.Client(
            base_url=self.base,
            headers={SESSION_HEADER: security.secret, "origin": self.base},
            cookies={security.cookie_name: security.secret},
            timeout=httpx.Timeout(600.0),
        )
        # nca_helpers.review_and_replay reads the replay job from the app's state.
        self.client.app = self.app

    def open_url(self) -> str:
        from opncafyr.core.bootstrap import mint_open_token, open_url

        return open_url(f"{self.base}/", mint_open_token(self.tokens))

    def close(self) -> None:
        self.client.close()
        self.server.should_exit = True
        self.thread.join(30)
        self.jobs.shutdown()
        self.supervisor.stop_all()


# --- the data flow (through the API, with the test helpers) -------------------------


def open_study(c) -> None:
    folder = c.client.post("/api/workspace/pick").json()["folder"]
    c.client.post("/api/workspace/init", json={"path": folder["path"]}).raise_for_status()
    c.client.post("/api/datasets/import", json={"path": SOURCE_PATH}).raise_for_status()


def wait_for_replay(c, run_id: str) -> None:
    """Let the launcher pick up the save, wait for its replay and confirm the run."""
    from nca_helpers import save_status, wait_job

    deadline = time.monotonic() + 120
    while (state := save_status(c.client, run_id)["run"]["state"]) != "reviewing":
        if time.monotonic() > deadline:
            raise RuntimeError(f"run {run_id} is {state} after saving")
        time.sleep(0.5)
    job = wait_job(c.client, c.app.state.nca.replays[run_id], timeout=600)
    if job["state"] != "completed":
        raise RuntimeError(job["message"])
    done = c.client.post(f"/api/nca/runs/{run_id}/complete")
    if done.status_code != 200:
        raise RuntimeError(done.text)


def print_failed_job_log(study: Path, lines: int = 30) -> None:
    """The end of the newest failed job's stderr, to say why a setup step failed."""
    import json

    jobs = sorted((study / ".opncafyr" / "jobs").glob("*/state.json"), key=os.path.getmtime)
    for state in reversed(jobs):
        if json.loads(state.read_text("utf-8")).get("state") == "failed":
            err = (state.parent / "stderr.log").read_text("utf-8", errors="replace")
            print("\n".join(err.splitlines()[-lines:]), file=sys.stderr)
            return


# --- opnca-r's own page (inside the launcher's frame) -------------------------------


def open_in_opnca(page, base: str, run_id: str, analyte: str):
    """Open the run from the Runs card, as a user would; returns opnca-r's frame."""
    go(page, base, "analysis", reload=True)
    page.get_by_role("button", name=f"Open run {run_id} in opnca-r").click()
    page.wait_for_url(re.compile(r"#/apps/opnca$"), timeout=300_000)
    frame = page.frame_locator("#tool-frames iframe[data-app=opnca]")
    frame.get_by_text(f"analyte: {analyte}").first.wait_for(timeout=300_000)
    return frame


def analyze_in_opnca(frame) -> None:
    """Analyze -> Run analysis, then the Profiles tab of the results."""
    frame.locator(".tab-button[data-tab=codegen]").click()
    frame.locator("#codegen-preview").click()
    frame.locator("#rs-body").wait_for(state="visible", timeout=300_000)
    frame.locator("#rs-tab-profiles").click()
    frame.locator("#rs-pane-profiles").locator("svg, canvas").first.wait_for(state="visible")


def save_in_opnca_page(frame) -> None:
    """Export -> Save, with the mean figures over nominal time, as the walkthrough says
    (opnca-r asks; it has no default)."""
    from playwright.sync_api import expect

    frame.locator(".tab-button[data-tab=export]").click()
    frame.locator("#ex-save-box").wait_for(state="visible")
    frame.locator("#ex-save-mean-controls select").first.select_option("nominal")
    frame.locator("#ex-save").click()
    expect(frame.locator("#ex-save-status")).to_contain_text("Saved ", timeout=300_000)


# --- capture ------------------------------------------------------------------------


class Shots:
    def __init__(self, out: Path, only: set[str]) -> None:
        self.out = out
        self.only = only
        self.made: dict[str, int] = {}
        self.skipped: dict[str, str] = {}

    def wanted(self, name: str) -> bool:
        return not self.only or name in self.only

    def skip(self, name: str, why: str) -> None:
        if self.wanted(name):
            self.skipped[name] = why
            print(f"skipped {name}: {why}", file=sys.stderr)

    def take(self, name: str, capture: Callable[[Path], None]) -> None:
        if not self.wanted(name):
            return
        path = self.out / f"{name}.png"
        try:
            capture(path)
        except Exception as exc:  # report it, keep going with the other images
            traceback.print_exc()
            self.skip(name, f"{type(exc).__name__}: {exc}".splitlines()[0])
            return
        size = path.stat().st_size
        if size > MAX_BYTES:
            print(f"warning: {name}.png is {size // 1024} KB (> 400 KB)", file=sys.stderr)
        self.made[name] = size
        print(f"wrote {path} ({size // 1024} KB)")


def region(page, *selectors: str, max_height: int = MAX_CROP_HEIGHT, pad: int = 8) -> dict:
    """The page-coordinate box around the given elements, cropped to ``max_height``."""
    box = page.evaluate(
        """(sels) => {
          const rs = sels.map((s) => document.querySelector(s).getBoundingClientRect());
          const top = Math.min(...rs.map((r) => r.top)) + scrollY;
          const left = Math.min(...rs.map((r) => r.left)) + scrollX;
          const bottom = Math.max(...rs.map((r) => r.bottom)) + scrollY;
          const right = Math.max(...rs.map((r) => r.right)) + scrollX;
          return {x: left, y: top, width: right - left, height: bottom - top};
        }""",
        list(selectors),
    )
    x, y = max(box["x"] - pad, 0), max(box["y"] - pad, 0)
    return {
        "x": x,
        "y": y,
        "width": box["width"] + 2 * pad,
        "height": min(box["height"] + 2 * pad, max_height),
    }


def clip(page, path: Path, *selectors: str, max_height: int = MAX_CROP_HEIGHT) -> None:
    for sel in selectors:
        page.locator(sel).first.wait_for()
    page.screenshot(
        path=str(path), full_page=True, clip=region(page, *selectors, max_height=max_height)
    )


def go(page, base: str, view: str, *, reload: bool = False) -> None:
    """Show ``#/<view>``; ``reload`` reloads the page so every card fetches fresh data."""
    page.goto(f"{base}/#/{view}")
    if reload:
        page.reload()
    page.wait_for_load_state("networkidle")


def choose_option(select, starts_with: str) -> None:
    """Pick the option of ``select`` whose label starts with ``starts_with``."""
    select.locator("option").filter(has_text=starts_with).first.wait_for(state="attached")
    labels = select.locator("option").all_inner_texts()
    label = next(t for t in labels if t.startswith(starts_with))
    select.select_option(label=label)


# --- the run ------------------------------------------------------------------------


def run(tmp: Path, runtime_file: Path, out: Path, only: set[str]) -> Shots:
    from playwright.sync_api import expect, sync_playwright

    from nca_helpers import ready_recipe, save_in_opnca, start_run
    from publish_helpers import writable

    shots = Shots(out, only)
    c = None
    try:
        c = Controller(tmp, runtime_file)
        base = c.base
        open_study(c)
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            context = browser.new_context(
                viewport=VIEWPORT, color_scheme="light", device_scale_factor=1
            )
            context.set_default_timeout(120_000)
            page = context.new_page()
            page.goto(c.open_url())
            page.locator("#ws-current").get_by_text(STUDY_NAME).first.wait_for()

            # 02: the Data page previewing the source file.
            def data_preview(path: Path) -> None:
                go(page, base, "data")
                page.get_by_role("button", name=f"Preview {SOURCE_PATH}").click()
                page.locator("#preview-body table").first.wait_for()
                expect(page.locator("#preview-heading")).to_contain_text(SOURCE_PATH)
                page.locator("#view-data h1").scroll_into_view_if_needed()
                page.screenshot(path=str(path))

            shots.take("02-data-preview", data_preview)

            # 03-05: the Analysis page on a recipe with the resolving steps and mapping.
            recipe_id = ready_recipe(c.client)
            # The practice study profile (16), followed before the runs are made so each
            # run records its profile step, as a user choosing it on Start analysis would.
            (c.study / "profiles").mkdir()
            (c.study / "profiles" / PRACTICE_PROFILE_NAME).write_text(PRACTICE_PROFILE, "utf-8")
            c.client.post(
                "/api/nca/profiles/select", json={"name": PRACTICE_PROFILE_NAME}
            ).raise_for_status()
            go(page, base, "analysis", reload=True)
            # One selection at a time: the card reloads the recipe list after a source is
            # chosen, and two overlapping loads would draw the result twice.
            with page.expect_response(lambda r: r.url.endswith("/api/nca/recipes")):
                choose_option(page.locator("#nca-recipe select").nth(0), SOURCE_PATH)
            choose_option(page.locator("#nca-recipe select").nth(1), recipe_id)
            steps_heading = page.locator("#nca-clean-result").get_by_role(
                "heading", name="Rows at each step"
            )
            steps_heading.first.wait_for()
            expect(steps_heading).to_have_count(1)
            page.locator("#nca-mapping").get_by_role("button", name="Save mapping").wait_for()

            shots.take(
                "03-cleaning",
                lambda path: clip(
                    page,
                    path,
                    "#nca-clean-result .card-head",
                    "#nca-clean-result table",
                    max_height=2000,
                ),
            )
            shots.take(
                "04-mapping",
                lambda path: clip(
                    page, path, "#nca-mapping .card-head", "#nca-mapping table", max_height=2000
                ),
            )

            def checks(path: Path) -> None:
                card = page.locator("#nca-analyte")
                choose_option(card.locator("select").nth(0), '"PARENT"')
                specimen = card.locator("select").nth(1)
                if specimen.is_visible():
                    choose_option(specimen, '"PLASMA"')
                card.get_by_role("button", name="Run checks").click()
                card.locator("#nca-findings li").first.wait_for()
                expect(card.locator(".card-status")).to_contain_text("blocking")
                clip(page, path, "#nca-analyte")

            shots.take("05-checks", checks)

            # 06: opnca-r inside the app on the PARENT run. Each run is opened from the
            # Runs card, analysed and saved in opnca-r's own page (so the figures are
            # opnca-r's), then replayed and confirmed through the API.
            def analyse(analyte: str, shot: str | None) -> str:
                step = f"run-{analyte.lower()}"
                run_id = start_run(c.client, recipe_id, analyte, profile_step=step)["run_id"]
                frame = open_in_opnca(page, base, run_id, analyte)
                try:
                    analyze_in_opnca(frame)
                    page.wait_for_load_state("networkidle")
                    if shot:
                        shots.take(shot, lambda path: page.screenshot(path=str(path)))
                    save_in_opnca_page(frame)
                except Exception:
                    traceback.print_exc()
                    print(f"note: saving {analyte} through the API instead", file=sys.stderr)
                    if shot and shot not in shots.made:
                        shots.take(shot, lambda path: page.screenshot(path=str(path)))
                    save_in_opnca(c.client, c.study, run_id, reopen=False)
                wait_for_replay(c, run_id)
                return run_id

            run_ids = {"parent": analyse("PARENT", "06-opnca"), "metab": analyse("METAB", None)}
            # Stop following the profile until 16, so the walkthrough's screens (07-15) show
            # no profile step; its progress is kept.
            c.client.post("/api/nca/profiles/clear").raise_for_status()

            # 07: both runs completed.
            def runs(path: Path) -> None:
                go(page, base, "analysis", reload=True)
                card = page.locator("#nca-runs")
                expect(card.locator("tbody tr")).to_have_count(2)
                expect(card.locator(".state-completed")).to_have_count(2)
                card.scroll_into_view_if_needed()
                clip(page, path, "#nca-runs")

            shots.take("07-runs", runs)

            # 08: the curator offering the completed runs' tables and figures.
            go(page, base, "publish", reload=True)
            curator = page.locator("#nca-curator")
            expect(curator.locator("fieldset")).to_have_count(2)
            shots.take("08-publish", lambda path: clip(page, path, "#nca-curator"))

            # Publish the pre-selected files, as a user would: Check, then Publish.
            curator.get_by_role("button", name="Check").click()
            expect(curator.locator(".card-status")).to_contain_text("ready to publish")
            curator.get_by_role("button", name="Publish").click()
            expect(curator.locator(".card-status")).to_contain_text("Recorded")

            # 09, 10: the Report and Deck cards after setup.
            def set_up(card_id: str, button: str, done: str) -> Callable[[Path], None]:
                def capture(path: Path) -> None:
                    card = page.locator(f"#{card_id}")
                    choose_option(card.locator("select").first, "pub-")
                    card.get_by_role("button", name=button).click()
                    status = card.locator(".card-status")
                    status.filter(has_text=re.compile(rf"{done}|failed|error|Error")).wait_for(
                        timeout=900_000
                    )
                    page.wait_for_load_state("networkidle")
                    card.scroll_into_view_if_needed()
                    clip(page, path, f"#{card_id}")
                    text = status.inner_text()
                    if done not in text:
                        print(f"note: {card_id} says: {text}", file=sys.stderr)
                        print_failed_job_log(c.study)

                return capture

            go(page, base, "publish", reload=True)
            shots.take(
                "09-report", set_up("nca-report", "Set up report", "Setting up the report finished")
            )
            shots.take("10-deck", set_up("nca-deck", "Set up deck", "Setting up the deck finished"))

            # 16: the Study profile part-way: checklist and mapping done, both slots filled
            # with the runs made for them, the publication chosen, the review still to do.
            # The analysis page has the recipe chosen, so the mapping hint can say "yes".
            def study_profile(path: Path) -> None:
                c.client.post(
                    "/api/nca/profiles/select", json={"name": PRACTICE_PROFILE_NAME}
                ).raise_for_status()
                api = "/api/nca/profiles/current"
                c.client.put(f"{api}/steps/ready", json={"items": ["plan", "imported"]})
                c.client.put(f"{api}/steps/map", json={"done": True}).raise_for_status()
                for slot, run_id in run_ids.items():
                    c.client.put(f"{api}/slots/{slot}", json={"run_id": run_id}).raise_for_status()
                pubs = c.client.get("/api/nca/publications").json()["publications"]
                pub = next(p["publication_id"] for p in pubs if p.get("state") == "recorded")
                c.client.put(
                    f"{api}/publications/publication", json={"publication_id": pub}
                ).raise_for_status()
                show_recipe()
                go(page, base, "profile")
                steps = page.locator("#nca-profile-steps")
                expect(steps).to_contain_text("5 of 6 steps done")
                expect(steps).to_contain_text("Hint: The saved column mapping blocks nothing: yes.")
                clip(page, path, "#nca-profile-steps", max_height=1600)

            def show_recipe() -> None:
                go(page, base, "analysis", reload=True)
                with page.expect_response(lambda r: r.url.endswith("/api/nca/recipes")):
                    choose_option(page.locator("#nca-recipe select").nth(0), SOURCE_PATH)
                choose_option(page.locator("#nca-recipe select").nth(1), recipe_id)
                page.locator("#nca-mapping").get_by_role("button", name="Save mapping").wait_for()
                page.locator("#nca-analyte select").first.wait_for()

            shots.take("16-study-profile", study_profile)
            # The walkthrough's screens show no profile step (17 shows Start analysis).
            c.client.post("/api/nca/profiles/clear").raise_for_status()

            # 17: the tutorial on "Choose exactly one analyte": the ring on the analyte
            # select and Next waiting for the checks. Skip moves through the earlier steps.
            def tutorial(path: Path) -> None:
                show_recipe()
                go(page, base, "tutorials")
                page.get_by_role("button", name="Start Your first analysis").click()
                panel = page.locator("#tutorial-panel")
                expect(panel).to_contain_text("Step 1 of 11")
                for n in range(2, 8):
                    panel.get_by_role("button", name="Skip", exact=True).click()
                    expect(panel).to_contain_text(f"Step {n} of 11")
                expect(panel).to_contain_text("Next is enabled when")
                expect(page.locator(".tutorial-target")).to_have_count(1)
                page.wait_for_timeout(500)  # the target scrolls into view
                page.screenshot(path=str(path))
                panel.get_by_role("button", name="Exit tutorial").click()

            shots.take("17-tutorial", tutorial)

            # 01: Home with recent runs and publications filled in.
            def home(path: Path) -> None:
                go(page, base, "", reload=True)
                page.locator("#runs-table").wait_for()
                page.locator("#pubs-table").wait_for()
                expect(page.locator("#runs-body tr")).to_have_count(2)
                page.screenshot(path=str(path), full_page=True)

            shots.take("01-home", home)

            # 11: Settings -> Support with the bundle preview open.
            def support(path: Path) -> None:
                go(page, base, "settings", reload=True)
                page.get_by_role("button", name="Create support bundle…").click()
                dialog = page.locator("#support-dialog")
                expect(dialog).to_be_visible()
                dialog.locator("details").first.wait_for()
                page.screenshot(path=str(path))
                page.keyboard.press("Escape")

            shots.take("11-support", support)

            # 12-14: Version history on the finished study folder: the launcher's page,
            # then gitneighbr's own page (local snapshots only, sending off).
            go(page, base, "history", reload=True)
            open_history = page.get_by_role("button", name="Open version history")
            open_history.wait_for()
            shots.take("12-history", lambda path: page.screenshot(path=str(path)))
            open_history.click()
            page.wait_for_url(re.compile(r"#/apps/gitneighbr$"), timeout=120_000)
            git = page.frame_locator("#tool-frames iframe[data-app=gitneighbr]")
            setup = git.get_by_role("region", name="Set up this folder")
            setup.get_by_role("button", name="Start version history here").wait_for()
            shots.take("13-history-setup", lambda path: setup.screenshot(path=str(path)))
            setup.get_by_role("button", name="Start version history here").click()
            git.locator("#identity-name").fill("A. Analyst")
            git.locator("#identity-email").fill("analyst@example.org")
            git.get_by_role("button", name="Save my name and email").click()
            git.get_by_role("region", name="Set up your Git identity").wait_for(state="detached")
            changes = git.get_by_role("region", name="Changed files")
            recipe = changes.get_by_role("checkbox", name="Include .opncafyr/recipes/").first
            recipe.wait_for()

            def changed(path: Path) -> None:
                changes.get_by_role("heading", name="Changes").scroll_into_view_if_needed()
                page.screenshot(path=str(path))

            shots.take("14-history-changes", changed)
            recipe.check()
            form = git.get_by_role("region", name="Save a snapshot")
            form.locator("#commit-summary").fill("Cleaning recipe for the PK analysis")
            shots.take("15-history-save", lambda path: form.screenshot(path=str(path)))
            context.close()
            browser.close()
    finally:
        if c is not None:
            c.close()
            writable(c.study)  # completed runs are sealed read-only
    return shots


def serve(tmp: Path, runtime_file: Path) -> int:
    """Run the app with an empty study folder until interrupted, for following the guide
    by hand. The folder picker always answers with that folder."""
    c = Controller(tmp, runtime_file, study_name="TA-walkthrough")
    urls = tmp / "open-urls.txt"
    urls.write_text("\n".join(c.open_url() for _ in range(20)) + "\n", "utf-8")
    print(f"study folder (the picker chooses it): {c.study}", flush=True)
    print(f"one-time links to open the app, one per line: {urls}", flush=True)
    print(f"open now: {c.open_url()}", flush=True)
    try:
        while c.thread.is_alive():
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        from publish_helpers import writable

        c.close()
        writable(c.study)  # completed runs are sealed read-only
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runtime", type=Path, help="runtime record (runtime-v1 JSON)")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="output folder")
    parser.add_argument("--only", nargs="+", choices=NAMES, metavar="NAME", default=[])
    parser.add_argument(
        "--check", action="store_true", help="list referenced images; exit 1 if any is missing"
    )
    parser.add_argument(
        "--serve",
        action="store_true",
        help="run the app with an empty study folder until interrupted (to follow the guide)",
    )
    args = parser.parse_args(argv)
    out = args.out.resolve()
    if args.check:
        return check(out)
    if args.runtime is None or not args.runtime.is_file():
        parser.error("--runtime must name a runtime record")
    out.mkdir(parents=True, exist_ok=True)
    # The top bar shows the study folder's path, and the images are published (the guide
    # and the docs site): use a shared, neutral folder rather than the per-user temp
    # folder (on macOS, /var/folders/<per-user id>/T/).
    # mkdtemp still makes a private (0700), randomly named folder inside it.
    shared = Path("/tmp")  # noqa: S108
    neutral = shared if os.name == "posix" and shared.is_dir() else None
    tmp = Path(tempfile.mkdtemp(prefix="opncafyr-guide-", dir=neutral))
    # Before anything from opncafyr is imported: keep the app's files out of the real root.
    os.environ["OPNCAFYR_APP_ROOT"] = str(tmp / "app root")
    sys.path.insert(0, str(TESTS))  # reuse the test helpers rather than copying them
    if args.serve:
        try:
            return serve(tmp, args.runtime.resolve())
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    try:
        shots = run(tmp, args.runtime.resolve(), out, set(args.only))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    for name, why in shots.skipped.items():
        print(f"skipped {name}: {why}")
    return 1 if shots.skipped else 0


if __name__ == "__main__":
    raise SystemExit(main())
