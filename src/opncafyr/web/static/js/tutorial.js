// Tutorials (#69, ADR 0010): a docked panel that points at controls and says what to do.
//
// The user does every step. This module only reads tutorial files (api/tutorials*) and
// writes the user's own progress (api/tutorials/progress). It never submits a form,
// clicks a control, calls a workflow endpoint or picks an option. Text is set with
// textContent; a step can name only a target, a view or a condition from the catalog.

import { getJSON, send } from "./api.js";
import { $, el, on, setStatus } from "./dom.js";
import { checkCondition, findTarget, guidanceCatalog, viewHash } from "./guidance.js";

const TICK_MS = 400;

let catalog = null;
let run = null; // { key, doc, index, paused, unhighlighted: Set<step id>, found: step id }
let highlighted = null;
let opener = null;
let timer = null;
let listing = null;

const panel = () => $("tutorial-panel");
const step = () => run.doc.steps[run.index];
const listStatus = (text, isError) => setStatus($("tutorials-status"), text, isError);

// Saves go one at a time, in the order they were made: a quick Next then Pause must not
// reach the controller as Pause then Next, or the pause is lost.
let saving = Promise.resolve();

function saveProgress({ active = true, finished = false } = {}) {
  const body = { key: run.key, step: step().id, paused: run.paused, active, finished };
  saving = saving.then(async () => {
    try {
      await send("PUT", "api/tutorials/progress", body);
    } catch (e) {
      $("tutorial-note").textContent = `Progress could not be saved: ${e.message}`;
    }
  });
  return saving;
}

function clearHighlight() {
  highlighted?.classList.remove("tutorial-target");
  highlighted = null;
}

// Highlight the step's target if it is on the page; otherwise explain and offer a way on.
function refreshHighlight() {
  const s = step();
  const recover = $("tutorial-recover");
  if (!s.target || run.unhighlighted.has(s.id)) {
    clearHighlight();
    recover.hidden = true;
    return;
  }
  const node = findTarget(s.target);
  if (node) {
    if (node !== highlighted) {
      clearHighlight();
      node.classList.add("tutorial-target");
      highlighted = node;
      if (run.found !== s.id) {
        run.found = s.id;
        node.scrollIntoView({ block: "center" });
      }
    }
    recover.hidden = true;
    return;
  }
  clearHighlight();
  recover.hidden = false;
}

function waitText(s) {
  if (!s.wait_for) return "";
  const met = checkCondition(s.wait_for);
  if (met === true) return "";
  const c = catalog.conditions.find((x) => x.id === s.wait_for);
  return `Next is enabled when: ${c ? c.title.toLowerCase() : "this step is done"}. Skip moves on anyway.`;
}

function missingText(s) {
  if (!s.target || run.unhighlighted.has(s.id) || findTarget(s.target)) return "";
  const t = catalog.targets.find((x) => x.id === s.target);
  return `“${t ? t.title : "The control"}” isn't on the page right now. It may appear after an ` +
    "earlier step, or it may not be in this version of the app or this state of the study.";
}

function refreshState() {
  if (!run || run.paused) return;
  refreshHighlight();
  const s = step();
  const next = $("tutorial-next");
  next.disabled = Boolean(s.wait_for) && checkCondition(s.wait_for) !== true;
  const note = [missingText(s), waitText(s)].filter(Boolean).join(" ");
  if ($("tutorial-note").textContent !== note) $("tutorial-note").textContent = note;
}

function showStep(moveFocus = true, save = true) {
  const s = step();
  run.paused = false;
  panel().classList.remove("paused");
  $("tutorial-pause").textContent = "Pause";
  let navigating = false;
  if (s.view && catalog.views.includes(s.view) && location.hash !== viewHash(s.view)) {
    navigating = true;
    location.hash = viewHash(s.view);
  }
  const last = run.index === run.doc.steps.length - 1;
  $("tutorial-name").textContent = `Tutorial: ${run.doc.title}`;
  $("tutorial-step-title").textContent = s.title;
  $("tutorial-count").textContent = `Step ${run.index + 1} of ${run.doc.steps.length}`;
  $("tutorial-text").textContent = s.text;
  $("tutorial-back").disabled = run.index === 0;
  $("tutorial-next").textContent = last ? "Finish" : "Next";
  $("tutorial-skip").textContent = last ? "Skip and finish" : "Skip";
  panel().hidden = false;
  refreshState();
  if (save) saveProgress();
  // The router focuses a new view's heading when it shows it (view-changed); the step
  // heading takes focus after that, so keyboard and screen-reader users stay in the panel.
  if (moveFocus && navigating) run.focusAfterView = true;
  else if (moveFocus) $("tutorial-step-title").focus();
}

function go(delta, { force = false } = {}) {
  if (!run) return;
  const s = step();
  if (delta > 0 && !force && s.wait_for && checkCondition(s.wait_for) !== true) return;
  const target = run.index + delta;
  if (target < 0) return;
  if (target >= run.doc.steps.length) return finish();
  run.index = target;
  showStep();
}

async function finish() {
  await saveProgress({ active: false, finished: true });
  close("You finished the tutorial. Start it again any time from Tutorials.");
}

function pause() {
  if (!run) return;
  if (run.paused) return showStep();
  run.paused = true;
  clearHighlight();
  panel().classList.add("paused");
  $("tutorial-pause").textContent = "Resume";
  $("tutorial-note").textContent = "Paused. Resume to carry on from this step.";
  $("tutorial-pause").focus();
  return saveProgress();
}

async function exit() {
  if (!run) return;
  await saveProgress({ active: false });
  close("Tutorial closed. Resume it any time from Tutorials.");
}

function close(message) {
  clearHighlight();
  clearInterval(timer);
  timer = null;
  run = null;
  panel().hidden = true;
  panel().classList.remove("paused");
  listStatus(message);
  refreshList();
  const back = opener && opener.isConnected ? opener : $("main");
  opener = null;
  back.focus();
}

// Open a tutorial at a step: its saved step if `resume`, else the first. With
// `onlyIfSame`, do nothing if the file is not the one the progress was saved for (a study
// tutorial's key is its file name, which another study folder may also have).
async function start(key, { resume = false, paused = false, onlyIfSame = false } = {}) {
  let found;
  let saved;
  try {
    catalog = await guidanceCatalog();
    found = await getJSON(`api/tutorials/file?key=${encodeURIComponent(key)}`);
    saved = (await getJSON("api/tutorials")).progress.tutorials[key];
  } catch (e) {
    return listStatus(e.message, true);
  }
  if (found.problems.length) return listStatus(`This tutorial can't be used: ${found.problems.join("; ")}`, true);
  if (onlyIfSame && (!saved || saved.sha256 !== found.sha256)) return;
  const ids = found.tutorial.steps.map((s) => s.id);
  let index = 0;
  let changed = false;
  if (resume && saved) {
    index = Math.max(0, ids.indexOf(saved.step));
    changed = saved.sha256 !== found.sha256;
  }
  if (!panel().contains(document.activeElement)) opener = document.activeElement;
  clearHighlight();
  run = { key, doc: found.tutorial, index, paused: false, unhighlighted: new Set(), found: null };
  clearInterval(timer);
  timer = setInterval(refreshState, TICK_MS);
  if (paused) {
    showStep(false, false); // pause() saves; one save, so the order can't flip
    pause();
  } else {
    showStep();
  }
  if (changed) {
    $("tutorial-note").textContent = "This tutorial changed since you last used it. It carries on from the same step if it still exists; Back goes to earlier steps.";
  }
}

// --- the Tutorials view ------------------------------------------------------------------

function progressText(p) {
  if (!p) return "Not started.";
  if (p.finished) return "Finished.";
  return `Stopped at step “${p.step}”${p.paused ? " (paused)" : ""}.`;
}

function tutorialItem(t, progress, workspaceOpen, studyNames) {
  const box = el("div", null, "panel");
  if (t.problems.length) {
    box.append(el("strong", `${t.key.replace(/^study\//, "tutorials/")} can't be used`));
    const list = el("ul");
    for (const p of t.problems) list.append(el("li", p));
    box.append(list);
    return box;
  }
  box.append(el("strong", t.title));
  const facts = [`${t.steps} steps`, `version ${t.version}`];
  if (t.audience) facts.push(`for ${t.audience.toLowerCase()}`);
  if (t.profile) facts.push(`goes with the study profile ${t.profile}`);
  box.append(el("p", facts.join(" · "), "muted"));
  if (t.prerequisites.length) {
    const list = el("ul");
    for (const p of t.prerequisites) list.append(el("li", p));
    box.append(el("p", "Before you start:"), list);
  }
  const p = progress.tutorials[t.key];
  box.append(el("p", progressText(p)));
  const actions = el("div", null, "actions");
  const button = (text, fn, cls = "btn btn-sm", label) => {
    const b = el("button", text, cls);
    b.type = "button";
    if (label) b.setAttribute("aria-label", label);
    b.addEventListener("click", fn);
    actions.append(b);
    return b;
  };
  if (p && !p.finished) {
    button("Resume", () => start(t.key, { resume: true }), "btn btn-primary btn-sm", `Resume ${t.title}`);
    button("Start again", () => start(t.key), "btn btn-sm", `Start ${t.title} again`);
  } else {
    button("Start", () => start(t.key), "btn btn-primary btn-sm", `Start ${t.title}`);
  }
  if (p) {
    button("Reset progress", async () => {
      try {
        await send("DELETE", `api/tutorials/progress?key=${encodeURIComponent(t.key)}`);
        listStatus(`Progress of ${t.title} reset.`);
        refreshList();
      } catch (e) { listStatus(e.message, true); }
    }, "btn btn-ghost btn-sm", `Reset progress of ${t.title}`);
  }
  if (t.key.startsWith("example/") && workspaceOpen) {
    const name = t.key.split("/").pop();
    const have = studyNames.has(name);
    const copy = button(have ? `tutorials/${name} exists` : "Copy into this study", async () => {
      try {
        const out = await send("POST", "api/tutorials/copy", { key: t.key });
        listStatus(`Copied to ${out.path}. Edit it in any text editor, then choose Check again.`);
        refreshList();
      } catch (e) { listStatus(e.message, true); }
    }, "btn btn-ghost btn-sm", have ? undefined : `Copy ${t.title} into this study`);
    copy.disabled = have;
  }
  box.append(actions);
  return box;
}

async function refreshList() {
  const list = $("tutorials-list");
  try {
    listing = await getJSON("api/tutorials");
  } catch (e) {
    return listStatus(e.message, true);
  }
  if (listing.progress.unreadable) {
    listStatus("The saved tutorial progress can't be read. Reset all tutorial progress to start again.", true);
  }
  const studyNames = new Set(listing.study.map((t) => t.key.split("/").pop()));
  list.replaceChildren();
  list.append(el("h3", "Examples"));
  for (const t of listing.examples) list.append(tutorialItem(t, listing.progress, listing.workspace_open, studyNames));
  list.append(el("h3", "This study's tutorials"));
  if (!listing.workspace_open) list.append(el("p", "Open a study folder to see its tutorials/ folder.", "muted"));
  else if (!listing.study.length) list.append(el("p", "None yet. Copy an example to start your own.", "muted"));
  for (const t of listing.study) list.append(tutorialItem(t, listing.progress, listing.workspace_open, studyNames));
}

// After a reload or a new launch, carry on with the tutorial that was open.
async function resumeActive() {
  if (run) return;
  let data;
  try {
    data = await getJSON("api/tutorials");
  } catch (e) {
    return;
  }
  const key = data.progress.active;
  if (!key) return;
  if (key.startsWith("study/") && !data.workspace_open) return; // wait for the folder
  const saved = data.progress.tutorials[key];
  await start(key, {
    resume: true,
    paused: Boolean(saved && saved.paused),
    onlyIfSame: key.startsWith("study/"),
  });
}

export function initTutorials() {
  $("tutorial-next").addEventListener("click", () => go(1));
  $("tutorial-skip").addEventListener("click", () => go(1, { force: true }));
  $("tutorial-back").addEventListener("click", () => go(-1));
  $("tutorial-pause").addEventListener("click", pause);
  $("tutorial-exit").addEventListener("click", exit);
  $("tutorial-retry").addEventListener("click", () => { if (run) { run.found = null; refreshState(); } });
  $("tutorial-nohighlight").addEventListener("click", () => {
    if (!run) return;
    run.unhighlighted.add(step().id);
    refreshState();
    $("tutorial-next").disabled ? $("tutorial-skip").focus() : $("tutorial-next").focus();
  });
  panel().addEventListener("keydown", (ev) => {
    if (ev.key === "Escape") { ev.preventDefault(); exit(); }
  });
  $("tutorials-refresh").addEventListener("click", () => { listStatus(""); refreshList(); });
  $("tutorials-reset-all").addEventListener("click", async () => {
    try {
      await send("DELETE", "api/tutorials/progress");
      listStatus("All tutorial progress reset.");
      refreshList();
    } catch (e) { listStatus(e.message, true); }
  });
  on("guidance-changed", refreshState);
  on("view-changed", (route) => {
    if (route.name === "tutorials") refreshList();
    if (!run) return;
    run.found = null;
    refreshState();
    if (run.focusAfterView) {
      run.focusAfterView = false;
      setTimeout(() => $("tutorial-step-title").focus(), 0); // after the router's own focus
    }
  });
  on("workspace-changed", () => {
    // A study tutorial belongs to the folder it came from: close it, keeping its place.
    if (run && run.key.startsWith("study/")) close("The study folder changed, so its tutorial was closed.");
    refreshList();
    resumeActive();
  });
  refreshList();
  resumeActive();
}
