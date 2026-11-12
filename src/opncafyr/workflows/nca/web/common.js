// Shared helpers for the NCA pages. Core helpers come from the shell's own modules.

export { getJSON, post, send, ApiError } from "../../js/api.js";
export { $, el, on, setStatus, stateBadge, formatTime } from "../../js/dom.js";
export { markTarget, notifyGuidance, registerCondition } from "../../js/guidance.js";
import { getJSON } from "../../js/api.js";
import { el } from "../../js/dom.js";

const TERMINAL = new Set(["completed", "failed", "cancelled"]);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// Wait for a job to finish, reporting its message as it goes. Returns the final record.
export async function waitForJob(jobId, onMessage) {
  for (;;) {
    const { job } = await getJSON(`api/jobs/${encodeURIComponent(jobId)}`);
    onMessage?.(job.message, job.state);
    if (TERMINAL.has(job.state)) return job;
    await sleep(500);
  }
}

// A raw value in quotes, so leading or trailing spaces stay visible.
export const raw = (value) => el("span", `"${value}"`, "raw");

export function cell(content, className) {
  const td = el("td", null, className);
  if (content instanceof Node) td.append(content);
  else td.textContent = content ?? "";
  return td;
}

export function table(headers, rows, caption) {
  const t = el("table");
  if (caption) t.append(el("caption", caption, "visually-hidden"));
  const head = el("thead");
  const tr = el("tr");
  for (const h of headers) {
    const th = el("th", h);
    th.scope = "col";
    tr.append(th);
  }
  head.append(tr);
  const body = el("tbody");
  for (const row of rows) {
    const r = el("tr");
    for (const c of row) r.append(c instanceof HTMLTableCellElement ? c : cell(c));
    body.append(r);
  }
  t.append(head, body);
  const wrap = el("div", null, "table-scroll");
  wrap.append(t);
  return wrap;
}

export function button(text, onClick, className = "btn btn-sm") {
  const b = el("button", text, className);
  b.type = "button";
  b.addEventListener("click", onClick);
  return b;
}

let ids = 0;
export const uid = (prefix) => `${prefix}-${++ids}`;

// A labelled field. `control` is an input/select/textarea or a container.
export function field(labelText, control, hint) {
  const wrap = el("div", null, "field");
  const id = control.id || uid("f");
  control.id = id;
  const label = el("label", labelText);
  label.htmlFor = id;
  wrap.append(label, control);
  if (hint) wrap.append(el("p", hint, "hint"));
  return wrap;
}

// A <select> with a "Choose…" placeholder and no default choice.
export function choice(options, value, placeholder = "Choose…") {
  const s = el("select");
  const blank = el("option", placeholder);
  blank.value = "";
  s.append(blank);
  for (const o of options) {
    const [v, label] = Array.isArray(o) ? o : [o, o];
    const opt = el("option", label);
    opt.value = v;
    if (v === value) opt.selected = true;
    s.append(opt);
  }
  return s;
}

// A numbered card. `target` is its guidance target id (ADR 0010), declared in __init__.py.
export function section(container, id, title, intro, target) {
  const card = el("section", null, "card");
  card.id = id;
  if (target) card.dataset.target = target;
  const head = el("div", null, "card-head");
  const h = el("h2", title);
  h.id = `${id}-heading`;
  head.append(h);
  card.setAttribute("aria-labelledby", h.id);
  card.append(head);
  if (intro) card.append(el("p", intro, "card-note"));
  const body = el("div", null, "card-body nca-body");
  const status = el("p", null, "status card-status");
  status.setAttribute("role", "status");
  status.setAttribute("aria-live", "polite");
  card.append(body, status);
  container.append(card);
  return { card, head, body, status };
}
