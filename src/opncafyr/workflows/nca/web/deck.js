// Deck (#33): a deckifyr project in deck/, bound to one publication. The editor runs under
// this origin; checks and builds are jobs whose results land in deck/builds/ with a
// manifest of the exact runs and file hashes they used.

import { button, el, field, getJSON, post, section, send, setStatus, waitForJob } from "./common.js";
import { emit } from "../../js/dom.js";
import { buildsTable, findingsTable, publicationChoice } from "./report.js";

export function initDeck(stack, state) {
  const sec = section(stack, "nca-deck", "5. Deck (PPTX)",
    "A deck project in deck/ with an NCA results slide for each published file. Edit it in the deck " +
      "editor, then check and build it here. Builds started inside the editor are drafts in deck/build/ " +
      "and are not recorded.");
  const info = el("div");
  const pick = el("div", null, "nca-row");
  const run = el("div", null, "nca-row");
  const results = el("div");
  const builds = el("div");
  sec.body.append(info, pick, run, results, builds);
  let status = null;
  let busy = false;

  async function job(jobId, what) {
    busy = true;
    draw();
    const done = await waitForJob(jobId, (message) => setStatus(sec.status, message));
    busy = false;
    setStatus(sec.status, done.state === "completed" ? `${what} finished.` : done.message, done.state !== "completed");
    await refresh();
  }

  async function act(fn) {
    try {
      await fn();
    } catch (e) {
      setStatus(sec.status, e.message, true);
    }
  }

  async function start(source) {
    const publication_id = select.value;
    if (!publication_id) return setStatus(sec.status, "Choose a publication first.", true);
    try {
      const out = await post("api/nca/deck", { publication_id, source });
      if (out.job_id) return job(out.job_id, "Setting up the deck");
      setStatus(sec.status, `The deck now uses ${publication_id}.`);
      refresh();
    } catch (e) {
      setStatus(sec.status, e.message, true);
      if (e.code === "needs_choice") {
        const row = el("div", null, "nca-row");
        row.append(
          button("Replace presentation.yaml", () => start("replace"), "btn btn-sm"),
          button("Keep my presentation.yaml", () => start("keep"), "btn btn-ghost btn-sm"));
        results.replaceChildren(row);
      }
    }
  }

  let select = publicationChoice(state);
  const setUp = button("Set up deck", () => start(null), "btn btn-sm");
  const open = button("Open editor", () => act(async () => {
    setStatus(sec.status, "Starting the deck editor…");
    await post("api/nca/deck/editor");
    setStatus(sec.status, "The deck editor is open. Save there before you check or build.");
    emit("app-restarted", "deckifyr");
    location.hash = "#/apps/deckifyr";
  }), "btn btn-sm");
  const close = button("Close editor", () => act(async () => {
    await send("DELETE", "api/nca/deck/editor");
    setStatus(sec.status, "The deck editor is closed; its edits were saved.");
    refresh();
  }), "btn btn-ghost btn-sm");
  const check = button("Check", () => act(async () => {
    const { job_id } = await post("api/nca/deck/check");
    await job(job_id, "The check");
  }), "btn btn-sm");
  const withPreviews = el("input");
  withPreviews.type = "checkbox";
  const previewLabel = el("label", null, "nca-row");
  previewLabel.append(withPreviews, "with slide previews");
  const build = button("Build", () => act(async () => {
    if (withPreviews.checked) setStatus(sec.status, "Checking that LibreOffice can make previews…");
    const { job_id } = await post("api/nca/deck/build", { previews: withPreviews.checked });
    await job(job_id, "The build");
  }), "btn btn-primary btn-sm");

  function draw() {
    const bound = status?.binding;
    const editing = status?.editor === "running" || status?.editor === "starting";
    info.replaceChildren();
    if (!status?.project) info.append(el("p", "No deck project yet.", "muted"));
    else if (bound) {
      info.append(el("p", `deck/ uses publication ${bound.publication_id}.${editing ? " The editor is open." : ""}`));
      if (status.edited.length) info.append(el("p", `Edited since it was set up: ${status.edited.join(", ")}.`, "hint"));
    }
    select = publicationChoice(state, bound?.publication_id);
    setUp.textContent = status?.project ? "Use this publication" : "Set up deck";
    pick.replaceChildren(field("Publication", select), setUp);
    run.replaceChildren(editing ? close : open, check, previewLabel, build);
    const previews = status?.previews || {};
    withPreviews.disabled = !previews.installed;
    previewLabel.title = previews.installed ? (previews.message || "") : "LibreOffice is not installed.";
    setUp.disabled = busy || editing;
    open.disabled = check.disabled = build.disabled = busy || !bound;
    results.replaceChildren();
    if (status?.check) {
      const f = status.check.findings;
      results.append(f.length ? findingsTable(f, "Deck check findings") : el("p", "The last check found nothing to fix.", "nca-ok"));
    }
    builds.replaceChildren();
    if (status?.builds.length) {
      builds.append(buildsTable(status.builds, "PPTX", (b) => `api/nca/deck/builds/${encodeURIComponent(b.build_id)}/pptx`));
    }
  }

  async function refresh() {
    try {
      status = await getJSON("api/nca/deck");
    } catch (e) {
      status = null;
      setStatus(sec.status, e.message, true);
    }
    draw();
  }

  state.onChange((what) => {
    if (["workspace", "shown", "publications-listed"].includes(what)) refresh();
  });
  refresh();
}
