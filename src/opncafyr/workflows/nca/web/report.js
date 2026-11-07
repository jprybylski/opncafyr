// Report (#32): a quartifyr project in report/, bound to one publication. Setting it up,
// checking it and rendering it are jobs; each render is kept in report/builds/ with a
// manifest of the exact runs and file hashes it used.

import { button, choice, el, field, formatTime, getJSON, post, section, setStatus, table, waitForJob } from "./common.js";

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

export function buildsTable(builds, kind, link) {
  return table(["Build", "Created", "Publication", "Runs", "Settings", ""], builds.map((b) => {
    const a = el("a", `Download ${kind}`);
    a.href = link(b);
    a.setAttribute("download", "");
    return [el("span", b.build_id, "path"), formatTime(b.created_at), el("span", b.publication_id, "path"),
      b.runs.join(", "), Object.values(b.settings || {}).join(", "), a];
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
  let busy = false;

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
  const level = choice([["draft", "Draft"], ["final", "Final"]], null, "Draft or final…");
  const check = button("Check", async () => {
    try {
      const { job_id } = await post("api/nca/report/check");
      await job(job_id, "The check");
    } catch (e) { setStatus(sec.status, e.message, true); }
  }, "btn btn-sm");
  const render = button("Render", async () => {
    if (!level.value) return setStatus(sec.status, "Choose draft or final.", true);
    try {
      const { job_id } = await post("api/nca/report/render", { status: level.value });
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
    run.replaceChildren(field("Status", level), check, render);
    for (const b of [setUp, check, render]) b.disabled = busy;
    check.disabled = render.disabled = busy || !bound;
    results.replaceChildren();
    if (status?.check) {
      const f = status.check.findings;
      results.append(f.length ? findingsTable(f, "Report check findings") : el("p", "The last check found nothing to fix.", "nca-ok"));
    }
    builds.replaceChildren();
    if (status?.builds.length) {
      builds.append(buildsTable(status.builds, "DOCX", (b) => `api/nca/report/builds/${encodeURIComponent(b.build_id)}/docx`));
    }
  }

  async function refresh() {
    try {
      status = await getJSON("api/nca/report");
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
