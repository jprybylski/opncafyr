// The in-app editor for guidance files (#76, ADR 0010): study profiles and tutorials as a
// form. Everything it draws comes from the server's spec (GET <api>/form), so this module
// names no file type.
//
// - Steps are added, moved and removed with buttons; targets, views and conditions are
//   picked from the catalog (GET api/guidance) by title, so the form can't make up an id.
// - The page never writes YAML. It sends the document to <api>/check as you type and to
//   <api>/edit on Save; the server writes the exact text it checked, only when nothing is
//   wrong, and never replaces a file that changed on disk.
// - Text is set with textContent only, including the preview.
// - Unsaved edits are reported with the page's heartbeat (unsaved.js), so Quit asks first.

import { getJSON, send } from "./api.js";
import { el, on, setStatus } from "./dom.js";
import { guidanceCatalog } from "./guidance.js";
import { changed, register } from "./unsaved.js";

const NAME_PATTERN = "[a-z][a-z0-9\\-]{0,63}";
const CHECK_MS = 500;
const NAME_RE = new RegExp(`^${NAME_PATTERN}$`);
const HANDLED = new Set(["id", "type", "title"]); // drawn at the top of every step
let ids = 0;
const uid = (prefix) => `${prefix}-${++ids}`;

const slug = (text) => {
  const s = String(text).toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^[^a-z]+|-+$/g, "");
  return s.slice(0, 48).replace(/-+$/, "") || "step";
};
const clone = (value) => JSON.parse(JSON.stringify(value));
const viewTitle = (v) => v.charAt(0).toUpperCase() + v.slice(1).replace(/-/g, " ");

function button(text, onClick, className = "btn btn-sm", label) {
  const b = el("button", text, className);
  b.type = "button";
  if (label) b.setAttribute("aria-label", label);
  b.addEventListener("click", onClick);
  return b;
}

function labelled(text, control, { required = false, help } = {}) {
  const wrap = el("div", null, "field");
  control.id = control.id || uid("gf");
  const label = el("label", required ? `${text} (required)` : text);
  label.htmlFor = control.id;
  wrap.append(label, control);
  if (help) {
    const hint = el("p", help, "hint");
    hint.id = uid("gf-hint");
    control.setAttribute("aria-describedby", hint.id);
    wrap.append(hint);
  }
  return wrap;
}

// Open the editor in `host`. `open` is {name} (a study file) or {example} (a new file
// from a shipped example). `onClose()` runs when the user closes it.
export async function openGuidanceForm(host, { api, open, unsavedKey, onClose, onSaved }) {
  const query = open.name ? `name=${encodeURIComponent(open.name)}` : `example=${encodeURIComponent(open.example)}`;
  const [{ spec, folder }, catalog, opened] = await Promise.all([
    getJSON(`${api}/form`), guidanceCatalog(), getJSON(`${api}/edit?${query}`),
  ]);
  const noun = spec.noun;
  const types = spec.step_types;
  const typeOf = (id) => (types || []).find((t) => t.id === id);
  const fieldsOf = (step) => [...spec.step, ...(types ? typeOf(step.type)?.fields || [] : [])];
  const common = new Set(spec.step.map((f) => f.name));

  const state = {
    doc: opened.doc ? clone(opened.doc) : null,
    baseline: opened.doc ? JSON.stringify(opened.doc) : "",
    name: opened.name,
    isNew: opened.new,
    sha256: opened.sha256,
    comments: opened.comment_lines,
    problems: opened.problems,
    stale: false, // the study folder changed while the editor was open
    autoIds: new WeakSet(), // steps whose id still follows their title
  };
  const dirty = () => Boolean(state.doc) && JSON.stringify(state.doc) !== state.baseline;
  let wasDirty = false;
  let unregister = register(`guidance-${unsavedKey}`, unsavedKey, dirty, () => save());
  let checkTimer = null;
  let closed = false;
  let checkSeq = 0;

  const card = el("section", null, "card guidance-form");
  const heading = el("h2");
  heading.id = uid("gf-heading");
  heading.tabIndex = -1;
  card.setAttribute("aria-labelledby", heading.id);
  const status = el("p", null, "status card-status");
  status.setAttribute("role", "status");
  status.setAttribute("aria-live", "polite");
  const say = (text, isError) => setStatus(status, text, isError);
  const body = el("div", null, "card-body");
  const problemsBox = el("div", null, "guidance-problems");
  problemsBox.setAttribute("aria-live", "polite");
  const preview = el("ol", null, "guidance-preview");
  const stepProblemNodes = [];
  const legends = [];
  let saveButton = null;
  let discardButton = null;
  let nameInput = null;

  host.hidden = false;
  host.replaceChildren(card);

  function close() {
    if (dirty() && !state.stale) {
      say("Save or discard your changes first.", true);
      return;
    }
    clearTimeout(checkTimer);
    if (unregister) unregister();
    unregister = null;
    closed = true;
    host.replaceChildren();
    host.hidden = true;
    onClose?.();
  }

  function noteDirty() {
    const now = dirty();
    if (saveButton) saveButton.disabled = state.stale || (!now && !state.isNew);
    if (discardButton) discardButton.disabled = !now;
    if (now !== wasDirty) {
      wasDirty = now;
      changed();
    }
  }

  // --- checking ------------------------------------------------------------------------

  function edited() {
    noteDirty();
    drawPreview();
    clearTimeout(checkTimer);
    checkTimer = setTimeout(check, CHECK_MS);
  }

  async function check() {
    const seq = ++checkSeq;
    try {
      const out = await send("POST", `${api}/check`, { doc: state.doc });
      if (seq !== checkSeq) return;
      state.problems = out.problems;
      drawProblems();
    } catch (e) {
      if (seq === checkSeq) say(`The ${noun} could not be checked: ${e.message}`, true);
    }
  }

  function focusStep(n) {
    const node = card.querySelector(`[data-step="${n}"] input, [data-step="${n}"] select`);
    if (node) {
      node.focus();
      node.scrollIntoView({ block: "center" });
    }
  }

  function drawProblems() {
    problemsBox.replaceChildren();
    for (const node of stepProblemNodes) node.replaceChildren();
    if (!state.problems.length) {
      problemsBox.append(el("p", `No problems found. Saving writes ${folder}/${state.name || "…"}.`, "muted"));
      return;
    }
    problemsBox.append(el("h3", state.problems.length === 1 ? "1 problem" : `${state.problems.length} problems`));
    const list = el("ul");
    for (const p of state.problems) {
      // The server counts steps from 0 ("steps/6"); the form numbers them from 1.
      const text = p.text.replace(/(^|[ /])steps\/(\d+)/, (m, pre, n) => `${pre}step ${Number(n) + 1}`);
      const li = el("li", text);
      if (p.step !== null && p.step < (state.doc?.steps || []).length) {
        li.append(" ", button(`Go to step ${p.step + 1}`, () => focusStep(p.step), "btn btn-ghost btn-sm"));
        const node = stepProblemNodes[p.step];
        if (node) node.append(el("li", text));
      }
      list.append(li);
    }
    problemsBox.append(list);
  }

  // --- the preview ---------------------------------------------------------------------

  function drawPreview() {
    preview.replaceChildren();
    for (const step of state.doc.steps) {
      const li = el("li");
      const type = types ? typeOf(step.type)?.title || "Unknown type" : null;
      li.append(el("strong", step.title || "Untitled step"));
      if (type) li.append(el("span", ` · ${type}`, "muted"));
      if (step.text) li.append(el("p", step.text));
      const facts = [];
      const target = catalog.targets.find((t) => t.id === step.target);
      if (step.view) facts.push(`Shows ${catalog.views.includes(step.view) ? viewTitle(step.view) : "a view not in this app"}`);
      if (step.target) facts.push(`Points at ${target ? target.title : "a control not in this app"}`);
      for (const key of ["wait_for", "hint_condition"]) {
        if (!step[key]) continue;
        const c = catalog.conditions.find((x) => x.id === step[key]);
        facts.push(`${key === "wait_for" ? "Waits until" : "Hint"}: ${c ? c.title : "a condition not in this app"}`);
      }
      if (step.slot) facts.push(`Run slot ${step.slot}`);
      if (Array.isArray(step.slots)) facts.push(`Publishes slots ${step.slots.join(", ") || "(none)"}`);
      if (Array.isArray(step.items)) facts.push(`${step.items.length} checklist item(s)`);
      if (facts.length) li.append(el("p", facts.join(" · "), "muted"));
      preview.append(li);
    }
  }

  // --- controls ------------------------------------------------------------------------

  function textControl(f, get, set) {
    const multi = f.kind === "text";
    const input = el(multi ? "textarea" : "input");
    if (!multi) input.type = "text";
    if (multi) input.rows = 3;
    input.maxLength = f.max || (f.kind === "name" ? 64 : f.kind === "version" ? 32 : 200);
    if (f.kind === "name") input.pattern = NAME_PATTERN;
    if (f.required) input.required = true;
    input.value = get() ?? "";
    input.addEventListener("input", () => {
      set(input.value === "" && !f.required ? undefined : input.value);
      edited();
    });
    return input;
  }

  // A catalog choice by title. An id this app doesn't have stays selected, labelled so,
  // until the user picks another; its text is never shown.
  function catalogControl(f, get, set, step) {
    const select = el("select");
    const blank = el("option", f.required ? "Choose…" : "None");
    blank.value = "";
    select.append(blank);
    const current = get() || "";
    let known = false;
    const add = (parent, value, label) => {
      const opt = el("option", label);
      opt.value = value;
      if (value === current) {
        opt.selected = true;
        known = true;
      }
      parent.append(opt);
    };
    if (f.kind === "view") {
      for (const v of catalog.views) add(select, v, viewTitle(v));
    } else if (f.kind === "condition") {
      for (const c of catalog.conditions) add(select, c.id, c.title);
    } else {
      // Targets by view. A tutorial step that shows a view lists that view's targets and
      // the ones on every page (the sidebar).
      const groups = new Map();
      for (const t of catalog.targets) {
        if (step && step.view && t.view && t.view !== step.view) continue;
        const g = t.view ? viewTitle(t.view) : "On every page";
        if (!groups.has(g)) groups.set(g, []);
        groups.get(g).push(t);
      }
      for (const [g, list] of groups) {
        const group = el("optgroup");
        group.label = g;
        for (const t of list) add(group, t.id, t.title);
        select.append(group);
      }
    }
    if (current && !known) {
      const other = catalog.targets.find((t) => t.id === current);
      const text = f.kind === "target" && other
        ? `${other.title} (on another view: choose again)`
        : `Not in this app: choose another ${f.kind}`;
      const opt = el("option", text);
      opt.value = current;
      opt.selected = true;
      select.append(opt);
    }
    select.addEventListener("change", () => {
      set(select.value || undefined);
      edited();
      if (f.kind === "view" && step) redraw({ step: state.doc.steps.indexOf(step), field: f.name });
      else drawPreview();
    });
    return select;
  }

  // Rows of single lines or of checklist items, each with Remove, and an Add button.
  function listControl(f, get, set, where) {
    const box = el("div", null, "guidance-rows");
    const items = f.kind === "items";
    const list = get() || [];
    list.forEach((row, i) => {
      const line = el("div", null, "guidance-row");
      if (items) {
        const id = textControl({ kind: "name", required: true }, () => row.id, (v) => { row.id = v ?? ""; });
        const text = textControl({ kind: "line", required: true }, () => row.text, (v) => { row.text = v ?? ""; });
        line.append(labelled(`Item ${i + 1} id`, id), labelled(`Item ${i + 1} text`, text));
      } else {
        const text = textControl({ kind: "line", required: true }, () => list[i], (v) => { list[i] = v ?? ""; });
        line.append(labelled(`${f.label}, line ${i + 1}`, text));
      }
      line.append(button("Remove", () => {
        list.splice(i, 1);
        set(list.length || f.required ? list : undefined);
        edited();
        redraw({ ...where, field: f.name, row: Math.min(i, list.length - 1) });
      }, "btn btn-ghost btn-sm", `Remove ${items ? "item" : "line"} ${i + 1}${where.step !== undefined ? ` of step ${where.step + 1}` : ""}`));
      line.dataset.row = String(i);
      box.append(line);
    });
    const max = f.max || 50;
    const add = button(items ? "Add item" : "Add line", () => {
      const used = new Set(list.map((r) => r.id));
      let n = list.length + 1;
      while (used.has(`item-${n}`)) n += 1;
      list.push(items ? { id: `item-${n}`, text: "" } : "");
      set(list);
      edited();
      redraw({ ...where, field: f.name, row: list.length - 1 });
    }, "btn btn-sm");
    add.disabled = list.length >= max;
    add.dataset.add = f.name;
    box.append(add);
    return box;
  }

  // Publication slots: checkboxes for the slots of earlier run steps.
  function slotsControl(f, step, index) {
    const set = el("fieldset", null, "guidance-slots");
    set.append(el("legend", `${f.label}${f.required ? " (required)" : ""}`));
    const chosen = new Set(step.slots || []);
    const earlier = state.doc.steps.slice(0, index).filter((s) => s.type === "run" && s.slot).map((s) => s.slot);
    const offered = [...new Set([...earlier, ...chosen])];
    if (!offered.length) set.append(el("p", "No run step comes before this one. Add a run step above it first.", "muted"));
    for (const slot of offered) {
      const label = el("label");
      const box = el("input");
      box.type = "checkbox";
      box.checked = chosen.has(slot);
      box.addEventListener("change", () => {
        const now = new Set(step.slots || []);
        if (box.checked) now.add(slot);
        else now.delete(slot);
        step.slots = offered.filter((s) => now.has(s));
        edited();
      });
      label.append(box, earlier.includes(slot) ? ` ${slot}` : ` ${slot} (no run step above has this slot)`);
      set.append(label);
    }
    return set;
  }

  function control(f, obj, where, step, index) {
    const get = () => obj[f.name];
    const set = (v) => {
      if (v === undefined) delete obj[f.name];
      else obj[f.name] = v;
    };
    let node;
    if (f.kind === "target" || f.kind === "view" || f.kind === "condition") node = catalogControl(f, get, set, step);
    else if (f.kind === "items" || f.kind === "lines") return wrapGroup(f, listControl(f, get, set, where));
    else if (f.kind === "slots") return slotsControl(f, step, index);
    else node = textControl(f, get, set);
    node.dataset.field = f.name;
    return labelled(f.label, node, { required: f.required, help: f.help });
  }

  function wrapGroup(f, inner) {
    const set = el("fieldset", null, "guidance-group");
    set.dataset.field = f.name;
    set.append(el("legend", `${f.label}${f.required ? " (required)" : ""}`), inner);
    return set;
  }

  // --- steps ---------------------------------------------------------------------------

  function uniqueId(base, except) {
    const used = new Set(state.doc.steps.filter((s) => s !== except).map((s) => s.id));
    let id = base;
    for (let n = 2; used.has(id); n += 1) id = `${base.slice(0, 60)}-${n}`;
    return id;
  }

  function newStep(type) {
    const step = { id: uniqueId("step"), title: "" };
    if (types) step.type = type;
    for (const f of fieldsOf(step)) {
      if (!f.required || f.name in step) continue;
      if (f.kind === "items") step.items = [{ id: "item-1", text: "" }];
      else if (f.kind === "slots") step.slots = [];
    }
    state.autoIds.add(step);
    return step;
  }

  function changeType(step, type, index) {
    const keep = new Set([...common, ...typeOf(type).fields.map((f) => f.name)]);
    const dropped = Object.keys(step).filter((k) => !keep.has(k));
    for (const k of dropped) delete step[k];
    step.type = type;
    for (const f of typeOf(type).fields) {
      if (f.required && !(f.name in step) && f.kind === "items") step.items = [{ id: "item-1", text: "" }];
      if (f.required && !(f.name in step) && f.kind === "slots") step.slots = [];
    }
    edited();
    redraw({ step: index, field: "type" });
    if (dropped.length) say(`Step ${index + 1} is now ${typeOf(type).title.toLowerCase()}; it no longer has: ${dropped.join(", ")}.`);
  }

  function move(index, delta) {
    const steps = state.doc.steps;
    const to = index + delta;
    [steps[index], steps[to]] = [steps[to], steps[index]];
    edited();
    redraw({ step: to, action: delta < 0 ? "up" : "down" });
    say(`Step moved to position ${to + 1}.`);
  }

  function remove(index) {
    const [gone] = state.doc.steps.splice(index, 1);
    edited();
    if (state.doc.steps.length) redraw({ step: Math.min(index, state.doc.steps.length - 1), field: "title" });
    else redraw({ action: "add" });
    say(`Removed step “${gone.title || gone.id}”.`);
  }

  function stepItem(step, i) {
    const n = state.doc.steps.length;
    const li = el("li", null, "guidance-step");
    li.dataset.step = String(i);
    const set = el("fieldset");
    const legend = el("legend", `Step ${i + 1}: ${step.title || "untitled"}`);
    legends[i] = legend;
    set.append(legend);

    const head = el("div", null, "guidance-step-head");
    if (types) {
      const select = el("select");
      for (const t of types) {
        const opt = el("option", t.title);
        opt.value = t.id;
        if (t.id === step.type) opt.selected = true;
        select.append(opt);
      }
      if (!typeOf(step.type)) {
        const opt = el("option", "Not a type this app has: choose one");
        opt.value = "";
        opt.selected = true;
        select.prepend(opt);
      }
      select.dataset.field = "type";
      select.addEventListener("change", () => select.value && changeType(step, select.value, i));
      head.append(labelled("Type", select, { required: true }));
    }
    const title = textControl({ kind: "line", required: true }, () => step.title, (v) => {
      step.title = v ?? "";
      legend.textContent = `Step ${i + 1}: ${step.title || "untitled"}`;
      if (state.autoIds.has(step)) {
        step.id = uniqueId(slug(step.title), step);
        if (idInput) idInput.value = step.id;
      }
    });
    title.dataset.field = "title";
    head.append(labelled("Title", title, { required: true }));
    const idInput = textControl({ kind: "name", required: true }, () => step.id, (v) => {
      step.id = v ?? "";
      state.autoIds.delete(step);
    });
    idInput.dataset.field = "id";
    head.append(labelled("Step id", idInput, { required: true, help: "Progress is kept by step id." }));
    set.append(head);

    for (const f of fieldsOf(step)) {
      if (HANDLED.has(f.name)) continue;
      set.append(control(f, step, { step: i }, step, i));
    }

    const problems = el("ul", null, "guidance-step-problems");
    stepProblemNodes[i] = problems;
    set.append(problems);

    const actions = el("div", null, "actions");
    const label = (verb) => `${verb} step ${i + 1}${step.title ? `, ${step.title}` : ""}`;
    const up = button("Move up", () => move(i, -1), "btn btn-ghost btn-sm", label("Move up"));
    up.disabled = i === 0;
    up.dataset.action = "up";
    const down = button("Move down", () => move(i, 1), "btn btn-ghost btn-sm", label("Move down"));
    down.disabled = i === n - 1;
    down.dataset.action = "down";
    const rm = button("Remove", () => remove(i), "btn btn-danger btn-sm", label("Remove"));
    rm.disabled = n === 1;
    rm.dataset.action = "remove";
    actions.append(up, down, rm);
    set.append(actions);
    li.append(set);
    return li;
  }

  // --- the whole form ------------------------------------------------------------------

  async function save() {
    if (state.stale) {
      say("The study folder changed after this editor was opened, so it can't save here. Close it and open the file again.", true);
      return false;
    }
    clearTimeout(checkTimer);
    const base = state.isNew ? (nameInput?.value || "").trim() : state.name.replace(/\.yaml$/, "");
    if (!NAME_RE.test(base)) {
      say("A file name is lower-case letters, digits and dashes, starting with a letter.", true);
      nameInput?.focus();
      return false;
    }
    const name = `${base}.yaml`;
    try {
      const out = await send("PUT", `${api}/edit`, { name, doc: state.doc, expected_sha256: state.sha256 });
      state.name = out.name;
      state.sha256 = out.sha256;
      state.isNew = false;
      state.comments = 0;
      state.problems = [];
      state.baseline = JSON.stringify(state.doc);
      redraw({ heading: true });
      say(`Saved ${out.path}.`);
      onSaved?.(out);
      return true;
    } catch (e) {
      if (e.code === "invalid" && Array.isArray(e.detail.problems)) {
        state.problems = e.detail.problems;
        drawProblems();
        say(e.message, true);
      } else if (e.code === "conflict" || (e.code === "not_found" && !state.isNew)) {
        drawConflict(e.message);
      } else if (e.code === "exists" || e.code === "invalid_name") {
        say(e.message, true);
        nameInput?.focus();
      } else {
        say(e.message, true);
      }
      return false;
    }
  }

  function drawConflict(message) {
    say(message, true);
    const box = el("div", null, "guidance-conflict");
    box.setAttribute("role", "alert");
    box.append(el("p", `${message} Your edits are still here.`));
    box.append(
      button("Save mine as a new file", () => {
        state.isNew = true;
        state.sha256 = null;
        state.name = state.name.replace(/\.yaml$/, "-edited.yaml");
        redraw({ name: true });
      }, "btn btn-sm"),
      " ",
      button("Open the file on disk (drops my edits)", async () => {
        if (unregister) unregister();
        unregister = null;
        closed = true;
        host.replaceChildren();
        await openGuidanceForm(host, { api, open: { name: state.name }, unsavedKey, onClose, onSaved });
      }, "btn btn-ghost btn-sm"),
    );
    body.prepend(box);
    box.querySelector("button").focus();
  }

  // Draw everything, then put focus back where the action came from.
  function redraw(focus = {}) {
    body.replaceChildren();
    stepProblemNodes.length = 0;
    legends.length = 0;
    const doc = state.doc;
    heading.textContent = state.isNew ? `New ${noun}` : `Edit ${noun}: ${doc.title || state.name}`;
    const intro = el("p", `Pick controls, views and conditions from the lists. The ${noun} is checked as you type, and Save writes it only when nothing is wrong. Saving never changes a run, a publication or anyone's progress.`, "card-note");
    body.append(intro);
    if (state.comments) {
      const lines = `${state.comments} comment line${state.comments === 1 ? "" : "s"} (starting with #)`;
      body.append(el("p", state.isNew
        ? `The example has ${lines}. The new file is written from this form, without them.`
        : `This file has ${lines}. Saving from this form rewrites the file without them.`, "guidance-notice"));
    }

    const file = el("div", null, "guidance-file");
    if (state.isNew) {
      nameInput = el("input");
      nameInput.type = "text";
      nameInput.pattern = NAME_PATTERN;
      nameInput.maxLength = 64;
      nameInput.required = true;
      nameInput.value = state.name.replace(/\.yaml$/, "");
      nameInput.addEventListener("input", () => { state.name = `${nameInput.value}.yaml`; drawProblems(); });
      file.append(labelled(`File name in ${folder}/ (.yaml is added)`, nameInput, {
        required: true, help: "Lower-case letters, digits and dashes. An existing file is never replaced.",
      }));
    } else {
      file.append(el("p", `File: ${folder}/${state.name}`, "muted"));
      nameInput = null;
    }
    body.append(file);

    const about = el("fieldset", null, "guidance-group");
    about.append(el("legend", `About this ${noun}`));
    for (const f of spec.document) about.append(control(f, doc, {}, null, -1));
    body.append(about);

    body.append(el("h3", "Steps"));
    const list = el("ol", null, "guidance-steps");
    doc.steps.forEach((step, i) => list.append(stepItem(step, i)));
    body.append(list);

    const addRow = el("div", null, "nca-row guidance-add");
    let typeSelect = null;
    if (types) {
      typeSelect = el("select");
      for (const t of types) {
        const opt = el("option", t.title);
        opt.value = t.id;
        typeSelect.append(opt);
      }
      addRow.append(labelled("New step type", typeSelect));
    }
    const add = button("Add step", () => {
      doc.steps.push(newStep(typeSelect?.value));
      edited();
      redraw({ step: doc.steps.length - 1, field: "title" });
      say(`Added step ${doc.steps.length}.`);
    }, "btn btn-sm");
    add.disabled = doc.steps.length >= spec.max_steps;
    add.dataset.action = "add";
    addRow.append(add);
    body.append(addRow);

    body.append(el("h3", "Check"), problemsBox);
    const previewBox = el("details", null, "guidance-preview-box");
    previewBox.append(el("summary", "Preview the steps"), preview);
    body.append(previewBox);

    const bar = el("div", null, "actions guidance-bar");
    saveButton = button(state.isNew ? `Save as new ${noun}` : "Save", () => save(), "btn btn-primary btn-sm");
    discardButton = button("Discard changes", () => {
      state.doc = JSON.parse(state.baseline);
      state.autoIds = new WeakSet();
      edited();
      redraw({ heading: true });
      say("Changes discarded.");
    }, "btn btn-ghost btn-sm");
    bar.append(saveButton, discardButton, button("Close", close, "btn btn-ghost btn-sm"));
    body.append(bar);

    drawProblems();
    drawPreview();
    noteDirty();
    restoreFocus(focus);
  }

  function restoreFocus(focus) {
    let node = null;
    if (focus.heading) node = heading;
    else if (focus.name) node = nameInput;
    else if (focus.action === "add") node = card.querySelector('[data-action="add"]');
    else if (focus.step !== undefined) {
      const scope = card.querySelector(`[data-step="${focus.step}"]`);
      if (scope && focus.action) {
        node = scope.querySelector(`[data-action="${focus.action}"]`);
        if (node?.disabled) node = scope.querySelector(`[data-action="${focus.action === "up" ? "down" : "up"}"]`);
      } else if (scope && focus.row !== undefined && focus.row >= 0) {
        node = scope.querySelector(`[data-field="${focus.field}"] [data-row="${focus.row}"] input`);
      } else if (scope && focus.field) {
        node = scope.querySelector(`[data-field="${focus.field}"] [data-add], [data-field="${focus.field}"]`);
      }
    } else if (focus.field) {
      const scope = about();
      node = scope?.querySelector(`[data-field="${focus.field}"] [data-row="${focus.row}"] input`) ||
        scope?.querySelector(`[data-field="${focus.field}"] [data-add]`);
    }
    node?.focus();
  }
  const about = () => body.querySelector("fieldset.guidance-group");

  card.append(heading, body, status);

  if (!state.doc) {
    heading.textContent = `${folder}/${opened.name} can't be opened in the form`;
    body.append(el("p", `The form opens a ${noun} only when its file is well formed. Fix these problems in a text editor, then choose Check again:`));
    const list = el("ul");
    for (const p of opened.problems) list.append(el("li", p.text));
    body.append(list, button("Close", close, "btn btn-sm"));
    heading.focus();
    return { close };
  }
  if (state.isNew) state.autoIds = new WeakSet();
  redraw({ heading: true });

  // Saving into another study than the one the file came from would be wrong: stop.
  on("workspace-changed", () => {
    if (closed || state.stale) return;
    state.stale = true;
    noteDirty();
    say("The study folder changed. This editor belonged to the previous study, so it can't save. Close it.", true);
  });
  return { close, dirty };
}
