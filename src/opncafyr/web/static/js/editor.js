// Project-file editor (#52): "#/tools/<id>/files". Any tool whose descriptor declares
// project_editor gets this view; everything shown comes from /api/tools/<id>/project, so
// this file never names a tool.
//
// - Source mode: the whole file in a text area with line numbers, find, the browser's
//   undo, explicit Save (Ctrl/Cmd+S) and Discard.
// - Visual mode (Markdown, Quarto, R Markdown): the file as blocks (mdblocks.js). Plain
//   headings, paragraphs and lists can be edited; everything else is locked and kept
//   byte for byte.
// - Preview: an approximate rendering built with DOM nodes only. It never runs code or
//   loads anything; the tool's own output is the authority.
// - A save sends the revision it opened. If the file changed on disk, both versions are
//   kept on screen until the user chooses.
// Unsaved text is reported with the page's heartbeat (unsaved.js), so the launcher won't
// close by itself and Quit asks first.

import { getJSON, post } from "./api.js";
import { $, el, emit, on, setStatus } from "./dom.js";
import * as md from "./mdblocks.js";
import { route } from "./router.js";
import { changed, register } from "./unsaved.js";

const VISUAL_SYNTAXES = new Set(["md", "qmd", "rmd"]);

const state = {
  tool: null,
  toolName: "",
  overview: null,
  file: null, // { path, label, syntax, sha256, saved, readonly, reason }
  buffer: "",
  mode: "source",
  blocks: null,
  signature: null,
  conflict: null, // { text, sha256 } of the disk version
  wasDirty: false,
};
let unregister = null;
let ui = null;

const api = (what) => `api/tools/${encodeURIComponent(state.tool)}/project${what}`;
const dirty = () => Boolean(state.file && !state.file.readonly && state.buffer !== state.file.saved);
const status = (text, isError) => setStatus($("files-status"), text, isError);

function noteDirty() {
  const now = dirty();
  $("files-dirty").textContent = now ? "Unsaved changes" : state.file ? "Saved" : "";
  $("files-dirty").classList.toggle("files-unsaved", now);
  if (ui) {
    ui.save.disabled = !now;
    ui.discard.disabled = !now;
  }
  if (now !== state.wasDirty) {
    state.wasDirty = now;
    changed();
    drawList();
  }
}

// --- the file list and settings ------------------------------------------------------

function drawList() {
  const list = $("files-list");
  list.replaceChildren();
  const files = state.overview?.files || [];
  if (!files.length) {
    list.append(el("li", state.overview ? "No files to edit." : "", "muted"));
    return;
  }
  for (const f of files) {
    const li = el("li");
    const b = el("button", f.label, "files-item");
    b.type = "button";
    if (state.file?.path === f.path) b.setAttribute("aria-current", "true");
    b.disabled = dirty() && state.file?.path !== f.path;
    b.title = b.disabled ? "Save or discard your changes first." : f.path;
    b.addEventListener("click", () => openFile(f.path));
    li.append(b);
    const meta = el("span", f.label === f.path ? `${f.bytes} bytes` : `${f.path} · ${f.bytes} bytes`, "files-meta");
    li.append(meta);
    if (f.readonly) li.append(el("span", `Read-only: ${f.reason}`, "files-meta"));
    list.append(li);
  }
}

function drawSettings() {
  const card = $("files-settings");
  const form = $("files-settings-form");
  const settings = state.overview?.settings;
  card.hidden = !settings;
  form.replaceChildren();
  if (!settings) return;
  if (settings.error) form.append(el("p", settings.error, "note"));
  for (const f of settings.fields) {
    const id = `files-setting-${f.name}`;
    const wrap = el("div", null, "field");
    const label = el("label", f.label);
    label.htmlFor = id;
    const select = el("select");
    select.id = id;
    const current = settings.values[f.name];
    if (current === null || current === undefined) select.append(new Option("Choose…", "", true, true));
    for (const [value, text] of f.options) select.append(new Option(text, value, false, value === current));
    select.addEventListener("change", () => saveSetting(f.name, select.value));
    wrap.append(label, select);
    if (f.help) wrap.append(el("p", f.help, "hint"));
    form.append(wrap);
  }
}

async function saveSetting(name, value) {
  const settings = state.overview.settings;
  const values = { ...settings.values, [name]: value || null };
  try {
    state.overview.settings = await post(api("/settings"), { values, expected_sha256: settings.sha256 });
    status("Settings saved.");
    emit("project-files-changed", { tool: state.tool });
  } catch (e) {
    status(e.code === "conflict" ? "The settings changed elsewhere; they have been reloaded." : e.message, true);
    await loadOverview();
  }
  drawSettings();
}

async function loadOverview() {
  state.overview = await getJSON(api(""));
  $("files-heading").textContent = state.overview.title;
  drawList();
  drawSettings();
}

// --- opening and saving --------------------------------------------------------------

async function openFile(path) {
  if (dirty() && state.file?.path !== path) {
    status(`Save or discard your changes to ${state.file.label} first.`, true);
    return;
  }
  try {
    const doc = await post(api("/read"), { path });
    state.file = { ...doc, saved: doc.text ?? "" };
    state.buffer = state.file.saved;
    state.conflict = null;
    state.mode = "source";
    status(doc.readonly ? `${doc.label} is read-only: ${doc.reason}` : "");
    drawEditor();
  } catch (e) {
    status(e.message, true);
  }
}

async function save() {
  if (!dirty()) return true;
  const { path } = state.file;
  try {
    const out = await post(api("/save"), { path, text: state.buffer, expected_sha256: state.file.sha256 });
    state.file.saved = state.buffer;
    state.file.sha256 = out.sha256;
    state.conflict = null;
    status(out.warnings.length ? `Saved, with notes: ${out.warnings.join(" ")}` : `Saved ${state.file.label}.`);
    emit("project-files-changed", { tool: state.tool, path });
    loadOverview().catch(() => {});
    drawConflict();
    noteDirty();
    return true;
  } catch (e) {
    if (e.code === "conflict") {
      await showConflict();
      return false;
    }
    status(e.message, true);
    return false;
  }
}

function discard() {
  if (!state.file) return;
  state.buffer = state.file.saved;
  state.conflict = null;
  status("Changes discarded.");
  drawEditor();
}

async function showConflict() {
  try {
    const disk = await post(api("/read"), { path: state.file.path });
    state.conflict = { text: disk.text ?? "", sha256: disk.sha256 };
  } catch (e) {
    state.conflict = { text: "", sha256: null };
  }
  status("The file changed on disk since you opened it. Nothing was saved; your text is still here.", true);
  drawConflict();
}

function drawConflict() {
  const box = ui?.conflict;
  if (!box) return;
  box.replaceChildren();
  box.hidden = !state.conflict;
  if (!state.conflict) return;
  box.append(el("h3", "The file changed on disk"));
  box.append(el("p", "Your unsaved text is in the editor. The version on disk is below. Choose which one to keep."));
  const disk = el("textarea", null, "files-disk");
  disk.readOnly = true;
  disk.value = state.conflict.text;
  disk.setAttribute("aria-label", "The version on disk");
  disk.rows = 8;
  const row = el("div", null, "files-actions");
  const mine = el("button", "Keep mine (replace the disk version)", "btn btn-sm");
  mine.type = "button";
  mine.disabled = !state.conflict.sha256;
  mine.addEventListener("click", () => {
    state.file.sha256 = state.conflict.sha256;
    save();
  });
  const theirs = el("button", "Use the disk version (discard mine)", "btn btn-ghost btn-sm");
  theirs.type = "button";
  theirs.addEventListener("click", () => {
    state.file.saved = state.conflict.text;
    state.file.sha256 = state.conflict.sha256;
    state.buffer = state.conflict.text;
    state.conflict = null;
    status("Loaded the version on disk.");
    drawEditor();
  });
  row.append(mine, theirs);
  box.append(disk, row);
}

// --- the editor ----------------------------------------------------------------------

function toolButton(text, title, fn) {
  const b = el("button", text, "btn btn-ghost btn-sm");
  b.type = "button";
  b.title = title;
  b.setAttribute("aria-label", title);
  // Keep the text selection in the editor when the button is pressed.
  b.addEventListener("mousedown", (ev) => ev.preventDefault());
  b.addEventListener("click", fn);
  return b;
}

// Insert text in the focused text area through the browser, so Undo still works.
function insert(area, before, after = "", placeholder = "text") {
  area.focus();
  const { selectionStart: s, selectionEnd: e, value } = area;
  const chosen = value.slice(s, e) || placeholder;
  const ok = document.execCommand && document.execCommand("insertText", false, before + chosen + after);
  if (!ok) {
    area.setRangeText(before + chosen + after, s, e, "end");
    area.dispatchEvent(new Event("input", { bubbles: true }));
  }
  area.setSelectionRange(s + before.length, s + before.length + chosen.length);
}

function heading(area) {
  area.focus();
  const start = area.value.lastIndexOf("\n", area.selectionStart - 1) + 1;
  area.setSelectionRange(start, start);
  const ok = document.execCommand && document.execCommand("insertText", false, "## ");
  if (!ok) {
    area.setRangeText("## ", start, start, "end");
    area.dispatchEvent(new Event("input", { bubbles: true }));
  }
}

function formatBar(target) {
  const bar = el("div", null, "files-format");
  bar.setAttribute("role", "toolbar");
  bar.setAttribute("aria-label", "Formatting");
  bar.append(
    toolButton("B", "Bold", () => target() && insert(target(), "**", "**")),
    toolButton("I", "Italic", () => target() && insert(target(), "*", "*")),
    toolButton("Link", "Link", () => target() && insert(target(), "[", "](https://)", "link text")),
    toolButton("H", "Heading", () => target() && heading(target())),
    toolButton("• List", "List item", () => target() && insert(target(), "\n- ", "", "item")),
  );
  return bar;
}

function lineNumbers(text) {
  const n = text.split("\n").length;
  let out = "";
  for (let i = 1; i <= n; i++) out += `${i}\n`;
  return out;
}

function sourceMode(body) {
  const wrap = el("div", null, "files-source");
  const gutter = el("pre", lineNumbers(state.buffer), "files-gutter");
  gutter.setAttribute("aria-hidden", "true");
  const area = el("textarea", null, "files-text");
  area.value = state.buffer;
  area.spellcheck = true;
  area.wrap = "off";
  area.readOnly = state.file.readonly;
  area.setAttribute("aria-label", `Source of ${state.file.label}`);
  let lines = state.buffer.split("\n").length;
  area.addEventListener("input", () => {
    state.buffer = area.value;
    const now = state.buffer.split("\n").length;
    if (now !== lines) {
      lines = now;
      gutter.textContent = lineNumbers(state.buffer);
    }
    noteDirty();
  });
  area.addEventListener("scroll", () => { gutter.scrollTop = area.scrollTop; });
  wrap.append(gutter, area);

  // Find: Enter for next, Shift+Enter for previous.
  const find = el("div", null, "files-find");
  const label = el("label", "Find");
  const input = el("input");
  input.type = "search";
  input.id = "files-find";
  label.htmlFor = input.id;
  const where = el("span", "", "files-meta");
  where.setAttribute("aria-live", "polite");
  const go = (back) => {
    const q = input.value;
    if (!q) return;
    const text = area.value;
    let at = back ? text.lastIndexOf(q, Math.max(0, area.selectionStart - 1)) : text.indexOf(q, area.selectionEnd);
    if (at < 0) at = back ? text.lastIndexOf(q) : text.indexOf(q);
    if (at < 0) {
      where.textContent = "Not found";
      return;
    }
    const line = text.slice(0, at).split("\n").length;
    where.textContent = `Line ${line}`;
    area.focus();
    area.setSelectionRange(at, at + q.length);
    const height = parseFloat(getComputedStyle(area).lineHeight) || 18;
    area.scrollTop = Math.max(0, (line - 1) * height - area.clientHeight / 2);
    input.focus();
  };
  input.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter") {
      ev.preventDefault();
      go(ev.shiftKey);
    }
  });
  const next = toolButton("Next", "Find next", () => go(false));
  const prev = toolButton("Previous", "Find previous", () => go(true));
  find.append(label, input, prev, next, where);

  if (!state.file.readonly) body.append(formatBar(() => area));
  body.append(find, wrap);
  return area;
}

function visualMode(body) {
  state.blocks = md.parse(state.buffer);
  state.signature = md.lockedSignature(state.blocks);
  let focused = null;
  body.append(formatBar(() => focused));
  body.append(el("p", "Plain headings, paragraphs and lists can be edited here. Locked parts are kept exactly as they are; change them in Source mode.", "hint"));
  const list = el("div", null, "md-blocks");
  state.blocks.forEach((block, index) => {
    if (block.kind === "blank") return;
    if (block.kind === "locked") {
      const fig = el("figure", null, "md-locked");
      fig.append(el("figcaption", `Locked: ${block.reason}`, "md-locked-label"));
      fig.append(el("pre", md.editable(block)));
      list.append(fig);
      return;
    }
    const area = el("textarea", null, `md-prose md-${block.type}`);
    area.value = md.editable(block);
    area.rows = Math.max(1, area.value.split("\n").length);
    area.setAttribute("aria-label", `${block.type} ${index + 1}`);
    const err = el("p", "", "md-error");
    err.hidden = true;
    area.addEventListener("focus", () => { focused = area; });
    area.addEventListener("input", () => {
      area.rows = Math.max(1, area.value.split("\n").length);
      const out = md.replaceBlock(state.blocks, index, area.value);
      if (!out.ok) {
        area.setAttribute("aria-invalid", "true");
        err.textContent = out.reason;
        err.hidden = false;
        return;
      }
      area.removeAttribute("aria-invalid");
      err.hidden = true;
      state.blocks = out.blocks;
      state.buffer = out.text;
      noteDirty();
    });
    list.append(area, err);
  });
  body.append(list);
}

function previewMode(body) {
  body.append(el("p", "An approximate preview. Nothing in the file is run; locked parts are only labelled. The tool's own output is what counts.", "hint"));
  const view = el("div", null, "md-preview");
  md.render(md.parse(state.buffer), view);
  body.append(view);
}

function drawEditor() {
  const body = $("files-editor-body");
  body.replaceChildren();
  ui = null;
  if (!state.file) {
    $("files-editor-heading").textContent = "Choose a file";
    noteDirty();
    return;
  }
  $("files-editor-heading").textContent = state.file.label;
  const bar = el("div", null, "files-bar");
  const modes = el("div", null, "files-modes");
  modes.setAttribute("role", "group");
  modes.setAttribute("aria-label", "View");
  const choices = [["source", "Source"]];
  if (VISUAL_SYNTAXES.has(state.file.syntax) && !state.file.readonly) choices.push(["visual", "Visual"]);
  if (VISUAL_SYNTAXES.has(state.file.syntax)) choices.push(["preview", "Preview"]);
  for (const [mode, text] of choices) {
    const b = el("button", text, "btn btn-ghost btn-sm");
    b.type = "button";
    b.setAttribute("aria-pressed", String(state.mode === mode));
    b.addEventListener("click", () => {
      state.mode = mode;
      drawEditor();
    });
    modes.append(b);
  }
  const save_ = el("button", "Save", "btn btn-primary btn-sm");
  save_.type = "button";
  save_.addEventListener("click", save);
  const discard_ = el("button", "Discard changes", "btn btn-ghost btn-sm");
  discard_.type = "button";
  discard_.addEventListener("click", discard);
  bar.append(modes, el("span", null, "spacer"), discard_, save_);
  const content = el("div", null, "files-content");
  const conflict = el("div", null, "files-conflict");
  conflict.setAttribute("role", "alert");
  body.append(bar, conflict, content);
  ui = { save: save_, discard: discard_, conflict };
  if (state.mode === "visual") visualMode(content);
  else if (state.mode === "preview") previewMode(content);
  else sourceMode(content);
  drawConflict();
  noteDirty();
}

// --- routing -------------------------------------------------------------------------

async function show(tool, file) {
  if (state.tool !== tool) {
    if (dirty()) {
      status(`Save or discard your changes to ${state.file.label} first.`, true);
      location.hash = `#/tools/${encodeURIComponent(state.tool)}/files`;
      return;
    }
    state.tool = tool;
    state.file = null;
    state.buffer = "";
    state.overview = null;
    if (unregister) unregister();
    unregister = register("project-files", tool, dirty, save);
    drawEditor();
  }
  status("");
  try {
    const tools = await getJSON("api/tools");
    const t = tools.tools.find((x) => x.id === tool);
    state.toolName = t ? t.display_name : tool;
    $("files-tool").textContent = state.toolName;
    await loadOverview();
    if (!state.overview.project_exists) {
      status(`There is no ${state.overview.title.toLowerCase()} to edit yet. Set it up first.`);
      return;
    }
    const wanted = file || (!state.file && state.overview.files[0]?.path);
    if (wanted && wanted !== state.file?.path) await openFile(wanted);
  } catch (e) {
    state.overview = null;
    drawList();
    drawSettings();
    status(e.code === "no_workspace" ? "Open a study folder first." : e.message, e.code !== "no_workspace");
  }
}

export function initEditor() {
  on("view-changed", (r) => {
    if (r.name === "files") show(r.param, r.file);
  });
  const opened = route(); // the page may have been opened on this view
  if (opened.name === "files") show(opened.param, opened.file);
  on("workspace-changed", () => {
    if (dirty()) return; // keep the text; a save shows what happened
    state.tool = null; // the next show() starts afresh
    state.file = null;
    state.buffer = "";
    state.overview = null;
    drawEditor();
    drawList();
    drawSettings();
    const r = route();
    if (r.name === "files") show(r.param, r.file);
  });
  document.addEventListener("keydown", (ev) => {
    if ((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase() === "s" && !$("view-files").hidden) {
      ev.preventDefault();
      save();
    }
  });
}
