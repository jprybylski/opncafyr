// Jobs (#12, #50): polls /api/jobs only while the page is visible and renders the
// activity card on Home, the full list on the Jobs view, the top-bar indicator and the
// sidebar count. The live region announces state changes, not every poll, and a
// re-render keeps keyboard focus.

import { getJSON, post } from "./api.js";
import { $, duration, el, emit, formatTime, on, pollWhileVisible, setStatus, stateBadge, stateWord } from "./dom.js";

const TERMINAL = new Set(["completed", "failed", "cancelled"]);
const MOVING = new Set(["preparing", "running", "reviewing"]);
const POLL_MS = 3000;
const HOME_FINISHED = 3; // finished jobs shown on Home under the live ones

let previous = null; // job_id -> state from the last poll
let lastSignature = "";
let poller = null;

function counts(jobs) {
  const waiting = jobs.filter((j) => j.state === "queued").length;
  const live = jobs.filter((j) => !TERMINAL.has(j.state)).length;
  return { live, running: live - waiting, waiting };
}

function summary(jobs) {
  const { live } = counts(jobs);
  if (!jobs.length) return "No jobs yet in this study folder.";
  if (!live) return "No jobs are running.";
  return live === 1 ? "1 job is running or waiting." : `${live} jobs are running or waiting.`;
}

function shortSummary(jobs) {
  const { running, waiting } = counts(jobs);
  const parts = [];
  if (running) parts.push(`${running} running`);
  if (waiting) parts.push(`${waiting} waiting`);
  return parts.join(" · ") || "No jobs running";
}

function changes(jobs) {
  if (previous === null) return { text: summary(jobs), finished: false };
  const lines = [];
  let finished = false;
  for (const job of jobs) {
    const before = previous.get(job.job_id);
    if (before === job.state) continue;
    if (TERMINAL.has(job.state)) finished = true;
    lines.push(`${job.label}: ${stateWord(job.state).toLowerCase()}.`);
  }
  return { text: lines.join(" "), finished };
}

async function cancel(job, button) {
  button.disabled = true;
  try {
    await post(`api/jobs/${encodeURIComponent(job.job_id)}/cancel`);
    setStatus($("jobs-live"), `Cancelling ${job.label}.`);
  } catch (e) {
    setStatus($("jobs-live"), e.message, true);
    button.disabled = false;
  }
  poller.refresh();
}

function timeEl(iso) {
  const t = el("time", formatTime(iso));
  t.dateTime = iso;
  return t;
}

function item(job) {
  const li = el("li", null, "job");
  li.dataset.jobId = job.job_id;

  const top = el("div", null, "job-top");
  top.append(el("strong", job.label, "job-label"), stateBadge(job.state));
  li.append(top);
  if (job.message) li.append(el("p", job.message, "job-msg"));
  if (MOVING.has(job.state)) {
    const bar = el("div", null, "bar");
    bar.setAttribute("aria-hidden", "true");
    bar.append(el("span"));
    li.append(bar);
  }

  const meta = el("div", null, "job-meta");
  const when = el("span");
  const started = job.started_at || job.created_at;
  if (started) {
    when.append(job.started_at ? "Started " : "Added ", timeEl(started));
    if (job.finished_at && job.started_at) when.append(` · ${duration(job.started_at, job.finished_at)}`);
  }
  meta.append(when);
  if (!TERMINAL.has(job.state)) {
    const button = el("button", "Cancel", "btn btn-sm");
    button.type = "button";
    button.dataset.jobId = job.job_id;
    button.setAttribute("aria-label", `Cancel ${job.label}`);
    button.addEventListener("click", () => cancel(job, button));
    meta.append(button);
  }
  li.append(meta);
  return li;
}

// Re-render a list and put focus back on the same job's Cancel button, or on a fallback.
function fill(list, jobs, fallback) {
  const active = document.activeElement;
  const focusedJob = active && list.contains(active) ? active.dataset.jobId : null;
  list.replaceChildren(...jobs.map(item));
  list.hidden = !jobs.length;
  if (focusedJob) {
    const again = list.querySelector(`button[data-job-id="${CSS.escape(focusedJob)}"]`);
    // The job finished and its button went away: keep focus nearby.
    (again || fallback).focus({ preventScroll: true });
  }
}

function showIndicator(jobs) {
  const { live } = counts(jobs);
  const link = $("live-jobs");
  link.classList.toggle("is-live", live > 0);
  $("live-jobs-text").textContent = shortSummary(jobs);
  const badge = $("nav-jobs-count");
  badge.hidden = !live;
  badge.replaceChildren(el("span", ", live: ", "visually-hidden"), String(live));
}

function render(data) {
  if (!data.workspace_open) {
    previous = null;
    for (const id of ["home-jobs", "jobs-list"]) $(id).hidden = true;
    for (const id of ["home-jobs-note", "jobs-note"]) {
      $(id).hidden = false;
      $(id).textContent = "Open a study folder to see its jobs.";
    }
    $("activity-summary").textContent = "";
    $("jobs-summary").textContent = "";
    showIndicator([]);
    setStatus($("jobs-live"), "Open a study folder to see its jobs.");
    return;
  }
  const jobs = data.jobs;
  const { text, finished } = changes(jobs);
  previous = new Map(jobs.map((j) => [j.job_id, j.state]));
  if (text) setStatus($("jobs-live"), text);
  if (finished) emit("job-finished");

  const live = jobs.filter((j) => !TERMINAL.has(j.state));
  const done = jobs.filter((j) => TERMINAL.has(j.state)).slice(0, HOME_FINISHED);
  const empty = jobs.length ? "" : "No jobs yet in this study folder.";
  for (const id of ["home-jobs-note", "jobs-note"]) {
    $(id).hidden = Boolean(jobs.length);
    $(id).textContent = empty;
  }
  fill($("home-jobs"), [...live, ...done], $("activity-heading"));
  fill($("jobs-list"), jobs, $("jobs-heading"));
  const short = jobs.length ? shortSummary(jobs) : "";
  $("activity-summary").textContent = short;
  $("jobs-summary").textContent = short;
  showIndicator(jobs);
}

async function refresh() {
  try {
    const data = await getJSON("api/jobs");
    const signature = JSON.stringify(data);
    if (signature === lastSignature) return;
    lastSignature = signature;
    render(data);
  } catch (e) {
    setStatus($("jobs-live"), "Could not load the jobs.", true);
  }
}

export function initJobs() {
  poller = pollWhileVisible(refresh, POLL_MS);
  on("workspace-changed", () => { previous = null; poller.refresh(); });
}
