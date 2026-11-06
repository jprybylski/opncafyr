// Job panel (#12): polls /api/jobs only while the page is visible. The live region
// announces state changes, not every poll, and a re-render keeps keyboard focus.

import { getJSON, post } from "./api.js";
import { $, el, emit, on, pollWhileVisible, setStatus, stateBadge, stateWord, timeCell } from "./dom.js";

const TERMINAL = new Set(["completed", "failed", "cancelled"]);
const POLL_MS = 3000;

let previous = null; // job_id -> state from the last poll
let lastSignature = "";
let poller = null;

function summary(jobs) {
  const live = jobs.filter((j) => !TERMINAL.has(j.state)).length;
  if (!jobs.length) return "No jobs yet in this study folder.";
  if (!live) return "No jobs are running.";
  return live === 1 ? "1 job is running or waiting." : `${live} jobs are running or waiting.`;
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

function row(job) {
  const tr = el("tr");
  tr.dataset.jobId = job.job_id;
  const state = el("td");
  state.append(stateBadge(job.state));
  const actions = el("td");
  if (!TERMINAL.has(job.state)) {
    const button = el("button", "Cancel");
    button.type = "button";
    button.dataset.jobId = job.job_id;
    button.setAttribute("aria-label", `Cancel ${job.label}`);
    button.addEventListener("click", () => cancel(job, button));
    actions.append(button);
  }
  tr.append(el("td", job.label), state, el("td", job.message || ""), timeCell(job.started_at || job.created_at), actions);
  return tr;
}

function render(data) {
  const table = $("jobs-table");
  if (!data.workspace_open) {
    table.hidden = true;
    previous = null;
    setStatus($("jobs-live"), "Open a study folder to see its jobs.");
    return;
  }
  const jobs = data.jobs;
  const { text, finished } = changes(jobs);
  previous = new Map(jobs.map((j) => [j.job_id, j.state]));
  if (text) setStatus($("jobs-live"), text);
  if (finished) emit("job-finished");

  const body = $("jobs-body");
  const active = document.activeElement;
  const focusedJob = active && body.contains(active) ? active.dataset.jobId : null;
  body.replaceChildren(...jobs.map(row));
  table.hidden = !jobs.length;
  if (focusedJob) {
    const again = body.querySelector(`button[data-job-id="${CSS.escape(focusedJob)}"]`);
    // The job finished and its button went away: keep focus in the panel.
    (again || $("jobs-heading")).focus({ preventScroll: true });
  }
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
  $("jobs-heading").tabIndex = -1;
  poller = pollWhileVisible(refresh, POLL_MS);
  on("workspace-changed", () => { previous = null; poller.refresh(); });
}
