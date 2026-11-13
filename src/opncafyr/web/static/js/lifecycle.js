// Lifecycle (#14): heartbeats while visible, Quit with its choices, the auto-close setting.
//
// The page is in one of four phases (#73):
//   running    the normal working page;
//   finishing  Quit was accepted and waits for running work; "Keep the app open" aborts it;
//   stopped    the server confirmed the stop (a Quit response, or the lifecycle says
//              it is stopping): the working page is replaced by the stopped page;
//   lost       requests fail and nothing confirmed a stop: the "connection lost" page.
// The last two are final. They need no request to show, and leave nothing focusable behind.

import { ApiError, getJSON, markStopped, onConnectionFailure, post, postAndForget, probe, send } from "./api.js";
import { $, el, emit, on, setStatus } from "./dom.js";
import { saveAll, unsavedTools } from "./unsaved.js";

const HEARTBEAT_MS = 15000;
const PENDING_POLL_MS = 5000;
const TERMINAL = new Set(["completed", "failed", "cancelled"]);

const randomId = () => (self.crypto && crypto.randomUUID ? crypto.randomUUID() : String(Math.random()).slice(2));
const PAGE_ID = randomId().replace(/[^A-Za-z0-9_-]/g, "");

let phase = "running";
// True once the server accepted a Quit (from this tab, or reported as pending by the
// lifecycle): a connection that drops after that is the stop we expected.
let stopExpected = false;
let heartbeatTimer = null;
let pendingTimer = null;
let checking = false;

const ended = () => phase === "stopped" || phase === "lost";
const appStatus = (text, isError) => setStatus($("app-status"), text, isError);
const lifecycle = () => getJSON("api/lifecycle");
const jobsText = (n) => (n === 1 ? "1 job" : `${n} jobs`);

// While an editor here holds unsaved text the page beats even when hidden, so the
// launcher keeps waiting for it (#52).
function beat() {
  const unsaved = unsavedTools();
  if (ended() || (document.visibilityState !== "visible" && !unsaved.length)) return Promise.resolve();
  return post("api/session/heartbeat", { page_id: PAGE_ID, unsaved }).catch(() => {});
}

// --- Final pages -----------------------------------------------------------------

const END = {
  stopped: {
    title: "Stopped · opncafyr",
    heading: "opncafyr has stopped",
    body: "This tab can no longer open studies or run work.",
    next: "To use opncafyr again, start it from your app shortcut. You can close this tab.",
  },
  lost: {
    title: "Connection lost · opncafyr",
    heading: "Connection to opncafyr was lost",
    body: "This tab can't reach opncafyr, so it can no longer open studies or run work.",
    next:
      "opncafyr may have been quit from another tab, closed itself after a time with no open " +
      "page, or stopped unexpectedly. Check whether it is still running; if it isn't, start " +
      "it from your app shortcut. You can close this tab.",
  },
};

// Replace the working page with the stopped or connection-lost page. `detail` states only
// what a server response confirmed.
function end(kind, detail) {
  if (ended()) return;
  phase = kind;
  markStopped();
  clearInterval(heartbeatTimer);
  clearInterval(pendingTimer);
  emit("app-stopped");

  for (const dialog of document.querySelectorAll("dialog[open]")) dialog.close();
  // Child apps lose their server too; their frames would only show errors.
  for (const frame of document.querySelectorAll("#tool-frames iframe")) frame.src = "about:blank";
  document.body.classList.remove("in-tool");
  // The class keeps them off screen even if a module later un-hides one (app.css).
  document.body.classList.add("app-ended");
  // Everything but the final page leaves the screen, the tab order and the accessibility tree.
  for (const node of document.body.children) {
    if (node.id === "end-view" || node.tagName === "SCRIPT" || node.classList.contains("sprite")) continue;
    node.inert = true;
    node.hidden = true;
  }

  const text = END[kind];
  $("end-title").textContent = text.heading;
  $("end-body").textContent = text.body;
  $("end-next").textContent = text.next;
  $("end-detail").textContent = detail || "";
  $("end-detail").hidden = !detail;
  $("end-actions").hidden = kind !== "lost";
  // The research notice moves with the page, in the header's own words.
  const notice = document.querySelector(".subbar .notice > span");
  if (notice && !$("end-notice").firstChild) $("end-notice").append(notice.cloneNode(true));
  const view = $("end-view");
  view.hidden = false;
  document.title = text.title;
  // Moving focus to the new heading tells a screen reader the whole page changed.
  $("end-title").focus();
}

// "Check again" on the connection-lost page: reload only if this launch answers again.
async function onRetry() {
  const status = $("end-retry-status");
  setStatus(status, "Checking…");
  try {
    const st = await probe("api/lifecycle");
    if (st.stopping) {
      setStatus(status, "opncafyr is stopping. Start it again from your app shortcut.");
      return;
    }
    location.reload();
  } catch (e) {
    setStatus(
      status,
      e instanceof ApiError
        ? "Something answered at this address, but not the opncafyr this tab was opened from. Start opncafyr from your app shortcut."
        : "opncafyr still isn't answering.",
    );
  }
}

// A request failed without any response. One direct look at the lifecycle decides.
async function checkConnection() {
  if (ended() || checking) return;
  checking = true;
  try {
    const st = await probe("api/lifecycle");
    if (st.stopping) end("stopped", stopDetail());
  } catch (e) {
    if (e instanceof ApiError) end("lost");
    else end(stopExpected ? "stopped" : "lost");
  } finally {
    checking = false;
  }
}

// A stop this tab didn't ask for, seen in the lifecycle.
const stopDetail = () => (stopExpected ? "" : "It was stopped from another tab, or closed itself after a time with no open page.");

// --- Finishing work before quitting --------------------------------------------------

async function showFinishing(st) {
  phase = "finishing";
  stopExpected = true;
  const box = $("quit-pending");
  const wasHidden = box.hidden;
  box.hidden = false;
  appStatus("");
  const parts = [];
  if (st.jobs) parts.push(`${jobsText(st.jobs)} still running`);
  if (st.busy?.length) parts.push(`${st.busy.join(", ")} still working`);
  const status = parts.length
    ? `opncafyr quits when this work finishes: ${parts.join(", ")}. New jobs can't be started.`
    : "opncafyr quits when the running work finishes. New jobs can't be started.";
  // Rewrite the live line only when it changes, so it is announced once per change.
  if ($("quit-pending-status").textContent !== status) $("quit-pending-status").textContent = status;
  if (wasHidden) watchPendingQuit();
  try {
    const data = await getJSON("api/jobs");
    const live = (data.jobs || []).filter((j) => !TERMINAL.has(j.state));
    $("quit-pending-jobs").replaceChildren(
      ...live.map((j) => el("li", j.message ? `${j.label}: ${j.message}` : j.label)),
    );
  } catch (e) {
    // The count above still stands; a lost connection is handled by checkConnection.
  }
}

function showRunning(text) {
  phase = "running";
  stopExpected = false;
  clearInterval(pendingTimer);
  $("quit-pending").hidden = true;
  $("quit-pending-status").textContent = "";
  $("quit-pending-jobs").replaceChildren();
  if (text !== undefined) appStatus(text);
}

// Follow the lifecycle while a quit is pending: it may finish, be aborted elsewhere, or stop.
function watchPendingQuit() {
  clearInterval(pendingTimer);
  pendingTimer = setInterval(async () => {
    if (ended()) return;
    let st;
    try {
      st = await lifecycle();
    } catch (e) {
      return; // a network failure reaches checkConnection through the API hook
    }
    if (st.stopping) end("stopped", stopDetail());
    else if (st.quit_pending) showFinishing(st);
    else showRunning("Quitting was cancelled. The app stays open.");
  }, PENDING_POLL_MS);
}

// --- Quit ------------------------------------------------------------------------

function ask(dialog) {
  return new Promise((resolve) => {
    dialog.returnValue = "";
    dialog.addEventListener("close", () => resolve(dialog.returnValue || "stay"), { once: true });
    dialog.showModal();
  });
}

async function quit(jobs, unsaved, savedHere) {
  let res;
  try {
    res = await post("api/quit", { jobs, unsaved });
  } catch (e) {
    if (ended() || !(e instanceof ApiError)) return; // a lost connection shows its own page
    if (e.code !== "needs_choice") {
      appStatus(`Quit didn't go through: ${e.message} The app stays open.`, true);
      return;
    }
    const where = (e.tools || []).join(", ") || "an editor";
    const reasons = e.detail.reasons || [];
    $("unsaved-text").textContent = reasons.includes("unsaved_in_page") && !unsavedTools().length
      ? `Another tab has unsaved changes in: ${where}. Save them there, or discard them and quit.`
      : `There are unsaved changes in: ${where}. Go back and save them, save them now and quit, or discard them and quit.`;
    const choice = await ask($("unsaved-dialog"));
    if (choice === "save") {
      // Editors in this page save themselves; child apps save as they stop.
      const here = unsavedTools().length > 0;
      if (!(await saveAll())) {
        appStatus("Some changes could not be saved. Quit was cancelled.", true);
        return;
      }
      await beat();
      await quit(jobs, "save", here);
    } else if (choice === "discard") {
      await quit(jobs, "discard", savedHere);
    } else {
      appStatus("Quit was cancelled. Your changes are still open.");
    }
    return;
  }
  stopExpected = true;
  if (res.status === "stopping_after_jobs") {
    showFinishing({ jobs: res.jobs || 0, busy: res.busy || [] });
    $("keep-open").focus();
    return;
  }
  const said = [];
  if (jobs === "cancel" && res.jobs) said.push(`${jobsText(res.jobs)} ${res.jobs === 1 ? "was" : "were"} cancelled.`);
  if (unsaved === "discard") said.push("Unsaved changes were discarded, as you chose.");
  else if (savedHere) said.push("Changes in this tab's editors were saved before quitting.");
  end("stopped", said.join(" "));
}

async function onQuit() {
  appStatus("");
  let jobs = "keep";
  try {
    const st = await lifecycle();
    if (st.stopping) {
      end("stopped", stopDetail());
      return;
    }
    if (st.jobs || st.busy.length) {
      const parts = [];
      if (st.jobs) parts.push(`${jobsText(st.jobs)} ${st.jobs === 1 ? "is" : "are"} still running`);
      if (st.busy.length) parts.push(`${st.busy.join(", ")} is still working`);
      $("jobs-text").textContent = `${parts.join(" and ")}. You can keep them running and quit when they finish, or cancel them and quit now.`;
      jobs = await ask($("jobs-dialog"));
      if (jobs === "stay") {
        appStatus("Quit was cancelled. The app stays open.");
        return;
      }
    }
  } catch (e) {
    if (!ended() && e instanceof ApiError) appStatus("Could not read the application state.", true);
    return;
  }
  await quit(jobs, "refuse", false);
}

async function onKeepOpen() {
  try {
    await post("api/quit/abort");
    showRunning("Quitting was cancelled. The app stays open.");
    $("quit").focus();
  } catch (e) {
    if (ended() || !(e instanceof ApiError)) return;
    // Too late: the stop has started.
    if (e.status === 409) end("stopped");
    else appStatus(e.message, true);
  }
}

async function onIdleChange(ev) {
  const v = ev.target.value;
  try {
    await send("PUT", "api/settings", { idle_timeout_minutes: v === "off" ? null : Number(v) });
    appStatus(v === "off" ? "The app will stay open until you quit it." : "Saved.");
  } catch (e) {
    appStatus(e.message, true);
  }
}

export function initLifecycle() {
  onConnectionFailure(() => checkConnection());
  heartbeatTimer = setInterval(beat, HEARTBEAT_MS);
  on("unsaved-changed", () => beat());
  document.addEventListener("visibilitychange", beat);
  window.addEventListener("pageshow", beat);
  // A hint only: unload events are unreliable, so the idle timer doesn't rely on it.
  window.addEventListener("pagehide", () => postAndForget("api/session/leave", { page_id: PAGE_ID }));
  beat();

  $("quit").addEventListener("click", onQuit);
  $("keep-open").addEventListener("click", onKeepOpen);
  $("idle").addEventListener("change", onIdleChange);
  $("end-retry").addEventListener("click", onRetry);

  lifecycle()
    .then((st) => {
      const sel = $("idle");
      const value = st.idle_timeout_minutes === null ? "off" : String(st.idle_timeout_minutes);
      if (![...sel.options].some((o) => o.value === value)) sel.append(new Option(`${value} minutes`, value));
      sel.value = value;
      if (st.stopping) end("stopped", stopDetail());
      else if (st.quit_pending) showFinishing(st);
    })
    .catch(() => {});
}
