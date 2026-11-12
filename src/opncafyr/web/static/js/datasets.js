// Data files in the open study folder (#21): shallow scan, read-only preview, import.
// The scan lists files directly in the folder and in source-data/. Analyte values are
// shown exactly as written, in quotes, because a run filters on the exact text.

import { getJSON, post } from "./api.js";
import { $, el, on, setStatus, stateBadge, timeCell } from "./dom.js";
import { notifyGuidance, registerCondition } from "./guidance.js";

const PREVIEW_ROWS = 20;
let anyImported = false; // for the guidance condition shell.dataset.imported

const TYPES = {
  integer: "Whole number",
  double: "Number",
  logical: "True/false",
  date: "Date",
  datetime: "Date-time",
  character: "Text",
};

let previewPath = null;
let loading = false;

const listStatus = (text, isError) => setStatus($("datasets-status"), text, isError);
const previewStatus = (text, isError) => setStatus($("preview-status"), text, isError);

function formatBytes(n) {
  if (!Number.isFinite(n)) return "";
  if (n < 1024) return `${n} B`;
  const units = ["KB", "MB", "GB"];
  let v = n / 1024;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i += 1; }
  return `${v.toFixed(v < 10 ? 1 : 0)} ${units[i]}`;
}

const count = (n) => Number(n).toLocaleString();

// A raw value in quotes, so leading or trailing spaces stay visible.
const raw = (value) => el("span", `"${value}"`, "raw");

function rowsText(d) {
  if (d.row_count === undefined) return "";
  return d.row_count_exact ? count(d.row_count) : `about ${count(d.row_count)}`;
}

function analyteCell(annotations) {
  const td = el("td");
  const columns = annotations?.analytes?.columns || [];
  for (const col of columns) {
    if (col.too_many_values) { td.append(el("span", `${col.column}: too many values`, "muted")); continue; }
    const span = el("span");
    span.append(`${col.column}: `);
    col.values.forEach((v, i) => { if (i) span.append(", "); span.append(raw(v.value)); });
    td.append(span);
  }
  return td;
}

function cell(content, className) {
  const td = el("td", null, className);
  if (content instanceof Node) td.append(content);
  else td.textContent = content ?? "";
  return td;
}

function fileRow(f) {
  const tr = el("tr");
  const name = el("span", f.path, "path");
  tr.append(cell(name));
  tr.append(cell(formatBytes(f.size_bytes), "num"));
  if (f.error) {
    const td = cell(f.error.message);
    td.colSpan = 3;
    tr.append(td, cell(""));
  } else {
    tr.append(cell(rowsText(f), "num"), cell(String(f.columns.length), "num"), analyteCell(f.annotations));
    tr.append(cell(f.imported ? stateBadge("recorded") : ""));
  }
  const button = el("button", "Preview", "btn btn-sm");
  button.type = "button";
  button.setAttribute("aria-label", `Preview ${f.path}`);
  button.disabled = Boolean(f.error);
  button.addEventListener("click", () => showPreview(f.path));
  tr.append(cell(button));
  return tr;
}

async function refreshDatasets() {
  if (loading) return;
  loading = true;
  try {
    const data = await getJSON("api/datasets");
    const table = $("datasets-table");
    const note = $("datasets-note");
    if (!data.workspace_open) {
      note.textContent = "Open a study folder to see its data files.";
      note.hidden = false;
      table.hidden = true;
      $("preview-section").hidden = true;
      previewPath = null;
      anyImported = false;
    } else {
      anyImported = data.files.some((f) => f.imported);
      const kinds = data.formats.map((f) => f.extensions.join(", ")).join(", ");
      note.textContent = data.files.length
        ? ""
        : `No data files (${kinds}) in the study folder or its source-data folder.`;
      note.hidden = !note.textContent;
      $("datasets-body").replaceChildren(...data.files.map(fileRow));
      table.hidden = !data.files.length;
    }
    listStatus("");
    notifyGuidance();
  } catch (e) {
    listStatus(`Could not scan the study folder. ${e.message}`, true);
  } finally {
    loading = false;
  }
}

function table(caption, headers, rows, className) {
  // Wide tables scroll sideways; the region is focusable so keyboards can scroll it.
  const wrap = el("div", null, "table-scroll");
  wrap.tabIndex = 0;
  wrap.setAttribute("role", "region");
  wrap.setAttribute("aria-label", caption);
  const t = el("table", null, className);
  t.append(el("caption", caption, "visually-hidden"));
  const head = el("tr");
  for (const h of headers) {
    const th = el("th", h);
    th.scope = "col";
    head.append(th);
  }
  const thead = el("thead");
  thead.append(head);
  const tbody = el("tbody");
  tbody.append(...rows);
  t.append(thead, tbody);
  wrap.append(t);
  return wrap;
}

function facts(d) {
  const dl = el("dl", null, "kv");
  const row = (k, v, cls) => {
    const dd = el("dd", null, cls);
    dd.append(v);
    dl.append(el("dt", k), dd);
  };
  row("File", d.path, "path");
  row("Size", `${formatBytes(d.size_bytes)} (${count(d.size_bytes)} bytes)`);
  const sha = el("span", d.sha256.slice(0, 16) + "…", "path");
  sha.title = d.sha256;
  row("SHA-256", sha);
  const rows = d.row_count_exact
    ? count(d.row_count)
    : `about ${count(d.row_count)} (estimated from the first ${count(d.rows_read)} rows)`;
  row("Rows", rows);
  row("Columns", String(d.columns.length));
  const details = d.details || {};
  if (details.delimiter_name) row("Separator", details.delimiter_name);
  if (details.utf8 !== undefined) {
    row("Text encoding", details.utf8 ? (details.bom ? "UTF-8 with byte-order mark" : "UTF-8") : "Not valid UTF-8");
  }
  if (details.missing_tokens) {
    const span = el("span");
    details.missing_tokens.forEach((t, i) => { if (i) span.append(" "); span.append(raw(t)); });
    row("Read as missing", span);
  }
  const time = timeCell(d.modified).firstChild;
  if (time) row("Last changed", time);
  return dl;
}

function warnings(d) {
  if (!d.warnings.length) return null;
  const box = el("div", null, "panel");
  box.setAttribute("role", "note");
  box.append(el("p", "Check before cleaning:"));
  const ul = el("ul");
  ul.append(...d.warnings.map((w) => el("li", w)));
  box.append(ul);
  return box;
}

function analytes(d) {
  const info = d.annotations?.analytes;
  if (!info) return null;
  const section = el("section");
  section.append(el("h3", "Candidate analytes"));
  if (!info.columns.length) {
    section.append(el("p", "No analyte code column was recognised. You will choose the analyte column when you set up a run.", "muted"));
    return section;
  }
  for (const col of info.columns) {
    if (col.too_many_values) {
      section.append(el("p", `${col.column} has too many values to list.`, "muted"));
      continue;
    }
    const rows = col.values.map((v) => {
      const tr = el("tr");
      tr.append(cell(raw(v.value)), cell(count(v.rows), "num"));
      return tr;
    });
    section.append(table(`Values of ${col.column}`, [`${col.column} value`, "Rows"], rows));
    for (const group of col.variants) {
      const p = el("p");
      p.append("These values differ only by spaces or letter case and stay separate analytes: ");
      group.forEach((v, i) => { if (i) p.append(", "); p.append(raw(v)); });
      section.append(p);
    }
  }
  const note = info.from_sample ? `${info.note} Counts are from the rows read.` : info.note;
  section.append(el("p", note, "muted"));
  return section;
}

function typeText(c) {
  let text = TYPES[c.type] || c.type;
  if (c.format) text += ` (${c.format})`;
  return text;
}

function columns(d) {
  const section = el("section");
  section.append(el("h3", "Columns"));
  const rows = d.columns.map((c) => {
    const tr = el("tr");
    const notes = el("span");
    notes.append(c.notes.join(" "));
    if (c.misfits.length) {
      notes.append(c.notes.length ? " For example " : "For example ");
      c.misfits.forEach((m, i) => { if (i) notes.append(", "); notes.append(raw(m)); });
      notes.append(".");
    }
    tr.append(cell(el("span", c.name, "path")), cell(typeText(c)), cell(count(c.n_missing), "num"), cell(notes));
    return tr;
  });
  section.append(table("Columns, types and missing values", ["Column", "Type", "Missing", "Notes"], rows));
  return section;
}

function firstRows(d) {
  const section = el("section");
  section.append(el("h3", d.head.length ? `First ${d.head.length} rows` : "No rows"));
  if (!d.head.length) return section;
  const rows = d.head.map((r, i) => {
    const tr = el("tr");
    tr.append(cell(String(i + 1), "num"), ...r.map((v) => cell(v)));
    return tr;
  });
  section.append(table("First rows of the file, as written", ["#", ...d.columns.map((c) => c.name)], rows, "rows-table"));
  return section;
}

async function showPreview(path) {
  const section = $("preview-section");
  previewPath = path;
  previewStatus("Reading the file…");
  section.hidden = false;
  try {
    const { dataset: d } = await post("api/datasets/preview", { path, rows: PREVIEW_ROWS });
    if (previewPath !== path) return;
    $("preview-heading").textContent = `Preview: ${d.path}`;
    $("preview-body").replaceChildren(
      ...[facts(d), warnings(d), analytes(d), columns(d), firstRows(d)].filter(Boolean),
    );
    $("preview-import").disabled = false;
    previewStatus("");
    $("preview-heading").focus();
  } catch (e) {
    $("preview-body").replaceChildren();
    $("preview-import").disabled = true;
    previewStatus(e.message, true);
  }
}

async function importCurrent() {
  if (!previewPath) return;
  const button = $("preview-import");
  button.disabled = true;
  try {
    const res = await post("api/datasets/import", { path: previewPath });
    previewStatus(
      res.created
        ? `Imported as source ${res.source.id}. Its size and SHA-256 are recorded in the workspace; the file itself is unchanged.`
        : `Already imported as source ${res.source.id}; the file has not changed since.`,
    );
    await refreshDatasets();
  } catch (e) {
    previewStatus(e.message, true);
  } finally {
    button.disabled = false;
  }
}

export function initDatasets() {
  registerCondition("shell.dataset.imported", () => anyImported);
  $("datasets-rescan").addEventListener("click", refreshDatasets);
  $("preview-import").addEventListener("click", importCurrent);
  on("workspace-changed", () => {
    $("preview-section").hidden = true;
    previewPath = null;
    refreshDatasets();
  });
  on("view-changed", (route) => { if (route.name === "data") refreshDatasets(); });
  refreshDatasets();
}
