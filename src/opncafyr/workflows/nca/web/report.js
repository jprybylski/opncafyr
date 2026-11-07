// Report (#32): a quartifyr project in report/, bound to one publication. Setting it up,
// checking it and rendering it are jobs; each render is kept in report/builds/ with a
// manifest of the exact runs and file hashes it used.
//
// Editing (#52): "Edit report" opens report.qmd in the launcher's project-file editor.
// Draft or final is the report's saved setting (the same settings the editor shows).
// Check and Render wait while the editor holds unsaved text, and a build whose inputs
// changed since is marked out of date.

import { on } from "../../js/dom.js";
import { anyUnsaved } from "../../js/unsaved.js";
import { button, choice, el, field, formatTime, getJSON, post, section, setStatus, table, waitForJob } from "./common.js";

const EDITOR_TOOL = "quartifyr";
const EDITOR = `#/tools/${EDITOR_TOOL}/files`;

// Shared with the deck card (#33).
export function publicationChoice(state, current) {
  const options = (state.publications || []).map((p) => [p.publication_id,
    `${p.publication_id}${p.label ? ` · ${p.label}` : ""} · ${(p.runs || []).map((r) => r.analyte.label || r.analyte.value).join(", ")}`]);
  return choice(options, current, "Choose a publication…");
}

export function findingsTable(findings, caption) {
  return table(["Level", "Check", "File", "Finding"], findings.map((f) => [
    f.severity === "error" ? "Blocks" : "Warning", f.check, el("span", f.path || "", "path"), f.message,
  ]), caption);
}

function settingsText(settings) {
  return Object.entries(settings || {}).map(([k, v]) =>
    `${k.replace(/_/g, " ")}: ${v === true ? "yes" : v === false ? "no" : v}`).join(", ");
}

// `stale` (optional): build id -> the inputs that changed since that build.
export function buildsTable(builds, kind, link, stale = {}) {
  return table(["Build", "Created", "Publication", "Runs", "Settings", ""], builds.map((b) => {
    const a = el("a", `Download ${kind}`);
    a.href = link(b);
    a.setAttribute("download", "");
    const id = el("span", null);
    id.append(el("span", b.build_id, "path"));
    const changed = stale[b.build_id] || [];
    if (changed.length) {
      const pill = el("span", "Out of date", "pill pill-warn pill-stale");
      pill.title = `Changed since this build: ${changed.join(", ")}`;
      id.append(pill);
    }
    const settings = { ...b.settings };
    delete settings.settings_sha256;
    delete settings.status_from;
    return [id, formatTime(b.created_at), el("span", b.publication_id, "path"),
      b.runs.join(", "), settingsText(settings), a];
  }), `${kind} builds`);
}

export function initReport(stack, state) {
  const sec = section(stack, "nca-report", "4. Report (DOCX)",
    "A quartifyr report project in report/, set up from the bundled template for one publication. " +
      "Check finds missing or broken metadata before rendering; a render that is incomplete keeps nothing.");
  const info = el("div");
  const pick = el("div", null, "nca-row");
  const run = el("div", null, "nca-row");
  const results = el("div");
  const builds = el("div");
  sec.body.append(info, pick, run, results, builds);
  let status = null;
  let settings = null; // the report settings from the editor API
  let busy = false;

  const unsavedNote = "The report editor has unsaved changes. Save or discard them first.";
  const blocked = () => anyUnsaved(EDITOR_TOOL);

  async function job(jobId, what) {
    busy = true;
    draw();
    const done = await waitForJob(jobId, (message) => setStatus(sec.status, message));
    busy = false;
    setStatus(sec.status, done.state === "completed" ? `${what} finished.` : done.message, done.state !== "completed");
    await refresh();
    return done;
  }

  async function start(source) {
    const publication_id = select.value;
    if (!publication_id) return setStatus(sec.status, "Choose a publication first.", true);
    try {
      const out = await post("api/nca/report", { publication_id, source });
      if (out.job_id) return job(out.job_id, "Setting up the report");
      setStatus(sec.status, `The report now uses ${publication_id}.`);
      refresh();
    } catch (e) {
      if (e.code === "needs_choice") {
        setStatus(sec.status, e.message, true);
        results.replaceChildren(el("div", null, "nca-row"));
        results.firstChild.append(
          button("Replace report.qmd", () => start("replace"), "btn btn-sm"),
          button("Keep my report.qmd", () => start("keep"), "btn btn-ghost btn-sm"));
        return;
      }
      setStatus(sec.status, e.message, true);
    }
  }

  let select = publicationChoice(state);
  const setUp = button("Set up report", () => start(null), "btn btn-sm");
  let savingLevel = Promise.resolve(true);
  let level = choice([["draft", "Draft"], ["final", "Final"]], null, "Draft or final…");
  async function saveLevel() {
    if (!settings) return true; // no project yet: the render request carries the status
    const values = { ...settings.values, status: level.value || null };
    try {
      settings = await post(`api/tools/${EDITOR_TOOL}/project/settings`, { values, expected_sha256: settings.sha256 });
      return true;
    } catch (e) {
      setStatus(sec.status, e.code === "conflict" ? "The report settings changed elsewhere; they have been reloaded." : e.message, true);
      await refresh();
      return false;
    }
  }
  const edit = el("a", "Edit report", "btn btn-sm");
  edit.href = `${EDITOR}?path=report.qmd`;
  const check = button("Check", async () => {
    if (blocked()) return setStatus(sec.status, unsavedNote, true);
    try {
      const { job_id } = await post("api/nca/report/check");
      await job(job_id, "The check");
    } catch (e) { setStatus(sec.status, e.message, true); }
  }, "btn btn-sm");
  const render = button("Render", async () => {
    if (blocked()) return setStatus(sec.status, unsavedNote, true);
    if (!level.value) return setStatus(sec.status, "Choose draft or final.", true);
    if (!(await savingLevel)) return;
    try {
      // The saved setting is what the render uses and records.
      const body = settings ? {} : { status: level.value };
      const { job_id } = await post("api/nca/report/render", body);
      await job(job_id, "The render");
    } catch (e) { setStatus(sec.status, e.message, true); }
  }, "btn btn-primary btn-sm");

  function draw() {
    const bound = status?.binding;
    info.replaceChildren();
    if (!status?.project) info.append(el("p", "No report project yet.", "muted"));
    else if (bound) {
      info.append(el("p", `report/ uses publication ${bound.publication_id}.`));
      if (status.edited.length) info.append(el("p", `Edited since it was set up: ${status.edited.join(", ")}.`, "hint"));
    }
    select = publicationChoice(state, bound?.publication_id);
    setUp.textContent = status?.project ? "Use this publication" : "Set up report";
    pick.replaceChildren(field("Publication", select), setUp);
    level = choice([["draft", "Draft"], ["final", "Final"]], settings?.values?.status ?? level.value ?? null, "Draft or final…");
    level.addEventListener("change", () => { savingLevel = saveLevel(); });
    run.replaceChildren(field("Status", level), check, render);
    if (status?.project) run.append(edit);
    for (const b of [setUp, check, render]) b.disabled = busy;
    check.disabled = render.disabled = busy || !bound;
    if (bound && blocked()) info.append(el("p", unsavedNote, "hint"));
    results.replaceChildren();
    if (status?.check) {
      const f = status.check.findings;
      results.append(f.length ? findingsTable(f, "Report check findings") : el("p", "The last check found nothing to fix.", "nca-ok"));
    }
    builds.replaceChildren();
    if (status?.builds.length) {
      builds.append(buildsTable(status.builds, "DOCX", (b) => `api/nca/report/builds/${encodeURIComponent(b.build_id)}/docx`, status.stale_inputs));
    }
  }

  async function refresh() {
    try {
      status = await getJSON("api/nca/report");
      settings = status.project ? (await getJSON(`api/tools/${EDITOR_TOOL}/project`)).settings : null;
    } catch (e) {
      status = null;
      settings = null;
      setStatus(sec.status, e.message, true);
    }
    draw();
  }

  on("project-files-changed", (d) => { if (d?.tool === EDITOR_TOOL) refresh(); });
  on("unsaved-changed", () => draw());
  state.onChange((what) => {
    if (["workspace", "shown", "publications-listed"].includes(what)) refresh();
  });
  refresh();
}
