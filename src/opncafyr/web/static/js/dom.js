// Small DOM and timing helpers shared by the home page modules.

export const $ = (id) => document.getElementById(id);

export function el(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined && text !== null) node.textContent = text;
  if (className) node.className = className;
  return node;
}

// A status line. Errors get a word prefix as well as a colour.
export function setStatus(node, text, isError) {
  node.replaceChildren();
  node.classList.toggle("error", Boolean(isError));
  if (!text) return;
  if (isError) node.append(el("strong", "Error: "));
  node.append(text);
}

// Every state is a symbol, a word and a tone. The tones (ok, warn, danger, info) and
// their colours are shared with the tools under /apps/, so a meaning learnt in one app
// carries over. Their internal "done" is shown here as Completed.
const STATES = {
  queued: ["◷", "Queued", "info"],
  preparing: ["◔", "Preparing", "info"],
  running: ["▶", "Running", "info"],
  reviewing: ["◑", "Reviewing", "info"],
  completed: ["✓", "Completed", "ok"],
  failed: ["✕", "Failed", "danger"],
  cancelled: ["■", "Cancelled", "warn"],
  incomplete: ["!", "Incomplete", "warn"],
  recorded: ["✓", "Recorded", "ok"],
  // Tool health (/api/tools)
  checking: ["◌", "Checking", "info"],
  ok: ["✓", "Available", "ok"],
  unavailable: ["✕", "Unavailable", "danger"],
};

// A state as a pill with a symbol and a word, so it never depends on colour.
export function stateBadge(state) {
  const [symbol, word, tone] = STATES[state] || ["•", String(state), "neutral"];
  const slug = String(state).replace(/[^a-z_]/g, "");
  const badge = el("span", null, `pill pill-${tone} state state-${slug}`);
  const icon = el("span", symbol);
  icon.setAttribute("aria-hidden", "true");
  badge.append(icon, " ", word);
  return badge;
}

// A tool's web app state (/api/apps), as a pill. "Ready" rather than "Running" keeps
// the job word "Running" (info) for work in progress.
const APP_STATES = {
  not_started: ["○", "Not running", "neutral"],
  starting: ["◔", "Starting", "info"],
  running: ["✓", "Ready", "ok"],
  crashed: ["✕", "Stopped unexpectedly", "danger"],
  stopped: ["■", "Stopped", "neutral"],
};

export function appBadge(state) {
  const [symbol, word, tone] = APP_STATES[state] || ["•", String(state), "neutral"];
  const badge = el("span", null, `pill pill-${tone} app-state`);
  const icon = el("span", symbol);
  icon.setAttribute("aria-hidden", "true");
  badge.append(icon, " ", word);
  return badge;
}

export function appStateWord(state) {
  return (APP_STATES[state] || [null, String(state)])[1];
}

// Routes from the API are absolute paths; keep links relative to the page (ADR 0006).
export const relativeRoute = (route) => route.replace(/^\/+/, "");

export function stateWord(state) {
  return (STATES[state] || [null, String(state)])[1];
}

// An icon from the page's SVG sprite (index.html), hidden from assistive technology.
export function icon(name, className = "icon") {
  const ns = document.querySelector("svg.sprite").namespaceURI;
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("class", className);
  svg.setAttribute("aria-hidden", "true");
  const use = document.createElementNS(ns, "use");
  use.setAttribute("href", `#i-${name}`);
  svg.append(use);
  return svg;
}

// "48 s", "2 min 14 s", "1 h 5 min".
export function duration(fromIso, toIso) {
  const ms = new Date(toIso).getTime() - new Date(fromIso).getTime();
  if (!Number.isFinite(ms) || ms < 0) return "";
  const s = Math.round(ms / 1000);
  if (s < 60) return `${s} s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} min ${s % 60} s`;
  return `${Math.floor(m / 60)} h ${m % 60} min`;
}

export function formatTime(iso) {
  if (!iso) return "";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

export function timeCell(iso) {
  const cell = el("td");
  if (!iso) return cell;
  const t = el("time", formatTime(iso));
  t.dateTime = iso;
  cell.append(t);
  return cell;
}

// Page-wide events between modules ("workspace-changed", "job-finished", "app-stopped").
const bus = new EventTarget();
export const emit = (name, detail) => bus.dispatchEvent(new CustomEvent(name, { detail }));
export const on = (name, fn) => bus.addEventListener(name, (ev) => fn(ev.detail));

// Call fn now and every `ms` while the page is visible; pause while it is hidden.
export function pollWhileVisible(fn, ms) {
  let timer = null;
  let running = false;
  let again = false;
  let stopped = false;
  const visible = () => document.visibilityState === "visible";

  async function tick() {
    clearTimeout(timer);
    timer = null;
    if (stopped || !visible()) return;
    if (running) { again = true; return; }
    running = true;
    try {
      await fn();
    } catch (e) {
      // fn reports its own errors.
    } finally {
      running = false;
      if (again) { again = false; tick(); }
      else if (!stopped && visible()) timer = setTimeout(tick, ms);
    }
  }

  document.addEventListener("visibilitychange", () => {
    if (visible()) tick();
    else { clearTimeout(timer); timer = null; }
  });
  on("app-stopped", () => { stopped = true; clearTimeout(timer); });
  tick();
  return { refresh: tick };
}
