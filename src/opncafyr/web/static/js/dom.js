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

const STATES = {
  queued: ["◷", "Queued"],
  preparing: ["◔", "Preparing"],
  running: ["▶", "Running"],
  reviewing: ["◑", "Reviewing"],
  completed: ["✓", "Completed"],
  failed: ["✕", "Failed"],
  cancelled: ["■", "Cancelled"],
  incomplete: ["!", "Incomplete"],
  recorded: ["✓", "Recorded"],
  // Tool health (/api/tools)
  checking: ["◌", "Checking"],
  ok: ["✓", "Available"],
  unavailable: ["✕", "Unavailable"],
};

// A state as a symbol and a word, so it never depends on colour.
export function stateBadge(state) {
  const [symbol, word] = STATES[state] || ["•", String(state)];
  const badge = el("span", null, `state state-${String(state).replace(/[^a-z_]/g, "")}`);
  const icon = el("span", symbol);
  icon.setAttribute("aria-hidden", "true");
  badge.append(icon, " ", word);
  return badge;
}

export function stateWord(state) {
  return (STATES[state] || [null, String(state)])[1];
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
