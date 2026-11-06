// Lifecycle (#14): heartbeats while visible, Quit with its choices, the auto-close setting.

import { getJSON, post, postAndForget, send } from "./api.js";
import { $, emit, setStatus } from "./dom.js";

const HEARTBEAT_MS = 15000;
const PENDING_POLL_MS = 5000;

const randomId = () => (self.crypto && crypto.randomUUID ? crypto.randomUUID() : String(Math.random()).slice(2));
const PAGE_ID = randomId().replace(/[^A-Za-z0-9_-]/g, "");

let stopped = false;
let pendingTimer = null;

const appStatus = (text, isError) => setStatus($("app-status"), text, isError);
const lifecycle = () => getJSON("api/lifecycle");

function beat() {
  if (stopped || document.visibilityState !== "visible") return;
  post("api/session/heartbeat", { page_id: PAGE_ID }).catch(() => {});
}

function showStopped() {
  stopped = true;
  clearInterval(pendingTimer);
  $("quit").disabled = true;
  $("idle").disabled = true;
  $("keep-open").hidden = true;
  appStatus("opncafyr has stopped. You can close this tab.");
  emit("app-stopped");
}

function ask(dialog) {
  return new Promise((resolve) => {
    dialog.returnValue = "";
    dialog.addEventListener("close", () => resolve(dialog.returnValue || "stay"), { once: true });
    dialog.showModal();
  });
}

function watchPendingQuit() {
  clearInterval(pendingTimer);
  pendingTimer = setInterval(async () => {
    try {
      const st = await lifecycle();
      if (!st.quit_pending && !st.stopping) {
        clearInterval(pendingTimer);
        return;
      }
      if (st.jobs) appStatus(`Quitting when ${st.jobs} job(s) finish.`);
    } catch (e) {
      showStopped();
    }
  }, PENDING_POLL_MS);
}

async function quit(jobs, unsaved) {
  try {
    const res = await post("api/quit", { jobs, unsaved });
    if (res.status === "stopping_after_jobs") {
      const n = res.jobs || 0;
      appStatus(n ? `Quitting when ${n} job(s) finish. New jobs can't be started.` : "Quitting when the running work finishes.");
      $("keep-open").hidden = false;
      watchPendingQuit();
    } else {
      showStopped();
    }
  } catch (e) {
    if (e.code !== "needs_choice") {
      appStatus(e.message, true);
      return;
    }
    const where = (e.tools || []).join(", ") || "an editor";
    $("unsaved-text").textContent = `There are unsaved changes in: ${where}. Go back and save them, or save them now and quit.`;
    if ((await ask($("unsaved-dialog"))) === "save") await quit(jobs, "save");
  }
}

async function onQuit() {
  appStatus("");
  let jobs = "keep";
  try {
    const st = await lifecycle();
    if (st.jobs || st.busy.length) {
      const parts = [];
      if (st.jobs) parts.push(`${st.jobs} job(s) are still running`);
      if (st.busy.length) parts.push(`${st.busy.join(", ")} is still working`);
      $("jobs-text").textContent = `${parts.join(" and ")}. You can keep them running and quit when they finish, or cancel them and quit now.`;
      jobs = await ask($("jobs-dialog"));
      if (jobs === "stay") return;
    }
  } catch (e) {
    appStatus("Could not read the application state.", true);
    return;
  }
  await quit(jobs, "refuse");
}

async function onKeepOpen() {
  try {
    await post("api/quit/abort");
    clearInterval(pendingTimer);
    $("keep-open").hidden = true;
    appStatus("The app stays open.");
    $("quit").focus();
  } catch (e) {
    appStatus(e.message, true);
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
  setInterval(beat, HEARTBEAT_MS);
  document.addEventListener("visibilitychange", beat);
  window.addEventListener("pageshow", beat);
  // A hint only: unload events are unreliable, so the idle timer doesn't rely on it.
  window.addEventListener("pagehide", () => postAndForget("api/session/leave", { page_id: PAGE_ID }));
  beat();

  $("quit").addEventListener("click", onQuit);
  $("keep-open").addEventListener("click", onKeepOpen);
  $("idle").addEventListener("change", onIdleChange);

  lifecycle()
    .then((st) => {
      const sel = $("idle");
      const value = st.idle_timeout_minutes === null ? "off" : String(st.idle_timeout_minutes);
      if (![...sel.options].some((o) => o.value === value)) sel.append(new Option(`${value} minutes`, value));
      sel.value = value;
      if (st.quit_pending) {
        $("keep-open").hidden = false;
        appStatus("Quitting when the running jobs finish.");
        watchPendingQuit();
      }
    })
    .catch(() => {});
}
