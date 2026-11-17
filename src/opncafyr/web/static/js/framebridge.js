// Embedded guidance, the launcher's side (ADR 0013, #77).
//
// A tutorial step may name a control inside an embedded app (a target whose catalog entry
// has "app"), or wait on a milestone the app reports. The launcher never reads or writes
// the app's page for this: it posts a fixed, versioned message to the app's own frame and
// the app outlines its own control, or answers, and replies.
//
//   to the app:   {type: "guidance", v: 1, op: "highlight" | "clear" | "condition", id, seq}
//   from the app: {type: "guidance-reply", v: 1, op, id, seq, state}
//
// - Messages go only to a frame toolview.js created, with this page's own origin as the
//   target origin, and carry only those fields: no HTML, script, selector or URL.
// - A reply counts only if it comes from that frame's window, on this origin, for a
//   request still waiting, in the fixed shape.
// - No reply within TIMEOUT_MS from a loaded page means the app has no hook (an older
//   version): "unsupported". "closed" means the app's frame isn't on screen (highlight)
//   or was never opened (condition).
//
// This is the only core file that posts to another window.

import { emit } from "./dom.js";
import { appFrame, shownFrame } from "./toolview.js";

const VERSION = 1;
const TIMEOUT_MS = 1000;
const HIGHLIGHT_STATES = new Set(["shown", "absent", "unknown"]);

let seq = 0;
const waiting = new Map(); // seq -> { key, op, frame, resolve, timer }
const known = new Map(); // "<app> <op> <id>" -> last state

const keyOf = (app, op, id) => `${app} ${op} ${id}`;

// The only message shape the launcher sends.
function message(op, id, n) {
  return op === "clear"
    ? { type: "guidance", v: VERSION, op: "clear", seq: n }
    : { type: "guidance", v: VERSION, op, id, seq: n };
}

function post(frame, op, id) {
  const n = ++seq;
  try {
    frame.contentWindow.postMessage(message(op, id, n), location.origin);
  } catch (e) {
    return null;
  }
  return n;
}

function remember(key, state) {
  const before = known.get(key);
  if (state === undefined) known.delete(key);
  else known.set(key, state);
  if (before !== state) emit("guidance-changed");
}

function settle(n, state) {
  const w = waiting.get(n);
  if (!w) return;
  waiting.delete(n);
  clearTimeout(w.timer);
  remember(w.key, state);
  w.resolve(state);
}

// Is the frame's page still loading? Its listener may not be there yet.
function loading(frame) {
  try {
    return frame.contentDocument.readyState !== "complete";
  } catch (e) {
    return false;
  }
}

function ask(app, op, id) {
  const frame = op === "highlight" ? shownFrame(app) : appFrame(app);
  const key = keyOf(app, op, id);
  if (!frame) {
    remember(key, "closed");
    return Promise.resolve("closed");
  }
  return new Promise((resolve) => {
    const n = post(frame, op, id);
    if (n === null) {
      remember(key, "closed");
      return resolve("closed");
    }
    const timer = setTimeout(() => settle(n, loading(frame) ? undefined : "unsupported"), TIMEOUT_MS);
    waiting.set(n, { key, op, frame, resolve, timer });
  });
}

window.addEventListener("message", (ev) => {
  if (ev.origin !== location.origin) return;
  const d = ev.data;
  if (!d || typeof d !== "object" || d.type !== "guidance-reply" || d.v !== VERSION) return;
  const w = waiting.get(d.seq);
  if (!w || ev.source !== w.frame.contentWindow || d.op !== w.op) return;
  const fits = w.op === "highlight"
    ? HIGHLIGHT_STATES.has(d.state)
    : d.state === true || d.state === false || d.state === "unknown";
  if (fits) settle(d.seq, d.state);
});

// Ask the app on screen to outline a control. Resolves with "shown", "absent", "unknown",
// "unsupported" or "closed"; repeating the request for a shown control changes nothing.
export const appHighlight = (app, id) => ask(app, "highlight", id);

// The last answer to appHighlight, or undefined while none has come.
export const appHighlightState = (app, id) => known.get(keyOf(app, "highlight", id));

// Ask the app to remove its outline (no reply), and forget what it last showed.
export function appClear(app) {
  for (const key of [...known.keys()]) {
    if (key.startsWith(`${app} highlight `)) known.delete(key);
  }
  const frame = appFrame(app);
  if (frame) post(frame, "clear");
}

// Whether a milestone the app reports holds: true, false, or undefined while unknown.
// Asks again each time; the answer arrives later and is announced as "guidance-changed".
export function appCondition(app, id) {
  ask(app, "condition", id);
  const state = known.get(keyOf(app, "condition", id));
  return state === true || state === false ? state : undefined;
}

// Is the app's frame the one on screen?
export const appShown = (app) => Boolean(shownFrame(app));
