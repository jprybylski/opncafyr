// Event records (#58): NONMEM-style files with separate dose and observation records.
// Shown inside "3. Result" after a full run of the recipe. Everything is explicit: the
// layout, columns and code meanings are suggestions until the user fills them in and
// saves; a choice the data needs has no default and blocks until it is made.

import {
  button, cell, choice, el, field, getJSON, send, setStatus, table,
} from "./common.js";

const PARTS = [
  ["subject", "Subject ID", true],
  ["time", "Actual time (of every record)", true],
  ["record_kind", "Record kind (EVID)", true],
  ["amount", "Dose amount (AMT)", true],
  ["endpoint", "Endpoint code (CMT, DVID)", true],
  ["route", "Route (read on dose records)", true],
  ["dv", "Observed value (DV)", false],
  ["mdv", "Missing-DV flag (MDV)", false],
  ["occasion", "Occasion", false],
  ["dose_unit", "Dose unit (read on dose records)", false],
  ["time_unit", "Time unit column", false],
];
const KINDS = [["observation", "Observation"], ["dose", "Dose"], ["other", "Other record"]];
const ENDPOINTS = [["pk", "PK analyte"], ["other", "Another endpoint"]];
const CHOICE_TEXT = {
  dose_interval: ["Dosing interval", [["first", "First dose"], ["last", "Last dose"], ["number", "Dose number…"]]],
  same_time_doses: ["Dose records at the same time", [["sum", "Sum them as one dose"]]],
  predose: ["Samples before the first dose", [["keep", "Keep (negative time after dose)"], ["set_aside", "Set aside"]]],
  observation_at_dose_time: ["A sample at a dose time belongs", [["before", "Before that dose"], ["after", "After that dose"]]],
  unmatched: ["PK observations with no dose", [["reject", "Reject (to the rejected-row log)"]]],
};
const PARTITION_TEXT = {
  selected: "Selected PK observations", predose_set_aside: "Predose, set aside",
  outside_interval: "In another dosing interval", dose: "Dose records",
  other_endpoint: "Other endpoints", other_record: "Other records",
  rejected: "Rejected (no dose)", unresolved: "Unresolved (blocking)",
};

// The panel for the recipe in `state`. `declare(value)` adds a constant analyte step.
export async function eventsPanel(state, declare) {
  const box = el("div", null, "nca-events");
  box.append(el("h3", "Event records"));
  const status = el("p", null, "status");
  status.setAttribute("role", "status");
  let help;
  try {
    help = await getJSON(`api/nca/recipes/${encodeURIComponent(state.recipe.id)}/events-help`);
  } catch (e) {
    box.append(el("p", e.message, "muted"));
    return box;
  }
  const s = help.suggestion;
  const saved = help.events;
  const editor = el("div");
  box.append(el("p", s.layout === "event_records"
    ? "This looks like event records (one row per dose or observation). Suggested, not applied:"
    : "This looks like one observation per row, mapped as it is.", "card-note"));
  if (s.reasons.length) {
    const ul = el("ul");
    for (const r of s.reasons) ul.append(el("li", r));
    box.append(ul);
  }
  if (s.needs_analyte && !saved) box.append(declareForm(declare));
  if (saved || s.layout === "event_records") {
    editor.append(form(state, help, status));
  } else {
    editor.append(button("Describe event records", () => {
      editor.replaceChildren(form(state, help, status));
    }));
  }
  box.append(editor, status);
  if (help.report) box.append(report(help));
  return box;
}

function declareForm(declare) {
  const wrap = el("div", null, "nca-row");
  const value = el("input");
  wrap.append(
    field("This file holds one analyte, named", value,
      "Adds an 'Add a constant column' step named analyte. Nothing is filled in for you."),
    button("Declare one analyte", () => {
      if (!value.value.trim()) return;
      declare(value.value);
    }, "btn btn-sm"),
  );
  const box = el("div");
  box.append(el("p", "No column looks like an analyte code.", "muted"), wrap);
  return box;
}

function form(state, help, status) {
  const s = help.suggestion;
  const saved = help.events || {};
  const cols = saved.columns || {};
  const wrap = el("div");
  const selects = {};
  const grid = el("div", null, "nca-row");
  for (const [part, label, required] of PARTS) {
    const sel = choice(help.columns, cols[part] || "", required ? "Choose…" : "None");
    selects[part] = sel;
    const hint = s.columns[part] ? `Suggested: ${s.columns[part].column}` : (required ? "No suggestion" : "");
    grid.append(field(`${label}${required ? " (required)" : ""}`, sel, hint));
  }
  const kindSel = {};
  const kindRows = s.record_kinds.map((k) => {
    const sel = choice(KINDS, (saved.record_kinds || {})[k.value] || "", "Not chosen");
    sel.setAttribute("aria-label", `Meaning of record kind "${k.value}"`);
    kindSel[k.value] = sel;
    return [cell(`"${k.value}"`), cell(String(k.rows), "nca-num"), k.suggested ? `Suggested: ${k.suggested}` : "", cell(sel)];
  });
  const endSel = {};
  const endRows = s.endpoints.map((e) => {
    const sel = choice(ENDPOINTS, (saved.endpoints || {})[e.value] || "", "Not chosen");
    sel.setAttribute("aria-label", `Meaning of endpoint "${e.value}"`);
    endSel[e.value] = sel;
    const rows = Object.entries(e.rows).map(([k, n]) => `${k}: ${n}`).join(", ");
    return [cell(`"${e.value}"`), rows, e.labels.join(", "), e.units.join(", "),
      e.only_on_doses ? "Only on dose records: not an analyte" : cell(sel)];
  });
  const mdvSel = {};
  const mdvRows = s.mdv_values.map((m) => {
    const sel = choice([["true", "No observation (true)"], ["false", "An observation (false)"]],
      m.saved === null || m.saved === undefined ? "" : String(m.saved), "Not chosen");
    sel.setAttribute("aria-label", `Meaning of MDV "${m.value}"`);
    mdvSel[m.value] = sel;
    return [cell(`"${m.value}"`), cell(String(m.rows), "nca-num"), cell(sel)];
  });
  const choices = saved.choices || {};
  const needed = new Set([...Object.keys(choices), ...help.findings.filter((f) => f.choice).map((f) => f.choice)]);
  needed.delete("dose_number");
  const choiceSel = {};
  const number = el("input");
  number.type = "number";
  number.min = "1";
  number.value = choices.dose_number || "";
  const choiceBox = el("div", null, "nca-row");
  for (const name of needed) {
    const [label, options] = CHOICE_TEXT[name];
    const sel = choice(options, choices[name] || "", "Not chosen");
    choiceSel[name] = sel;
    choiceBox.append(field(label, sel));
    if (name === "dose_interval") choiceBox.append(field("Dose number (for Dose number…)", number));
  }

  const useSuggestions = button("Use the suggestions", () => {
    for (const [part, sel] of Object.entries(selects)) if (!sel.value && s.columns[part]) sel.value = s.columns[part].column;
    for (const k of s.record_kinds) if (!kindSel[k.value].value && k.suggested) kindSel[k.value].value = k.suggested;
  }, "btn btn-ghost btn-sm");

  const save = button("Save event records", async () => {
    const columns = {};
    for (const [part, sel] of Object.entries(selects)) if (sel.value) columns[part] = sel.value;
    const pick = (table) => Object.fromEntries(Object.entries(table).filter(([, sel]) => sel.value).map(([v, sel]) => [v, sel.value]));
    const events = { columns, record_kinds: pick(kindSel), endpoints: pick(endSel) };
    if (columns.mdv) {
      events.mdv = Object.fromEntries(Object.entries(mdvSel).filter(([, sel]) => sel.value).map(([v, sel]) => [v, sel.value === "true"]));
    }
    const chosen = {};
    for (const [name, sel] of Object.entries(choiceSel)) if (sel.value) chosen[name] = sel.value;
    if (chosen.dose_interval === "number") chosen.dose_number = Number(number.value);
    events.choices = chosen;
    await put(state, events, status, "Saved. Apply to all rows again to prepare the observations.");
  }, "btn btn-primary btn-sm");
  const remove = button("Remove event records", () => put(state, null, status, "Removed. Apply to all rows again."), "btn btn-ghost btn-sm");
  remove.hidden = !help.events;

  wrap.append(el("h4", "Columns"), grid);
  wrap.append(el("h4", "Record kinds"), el("p", "Every value needs a meaning; EVID 3 and 4 are never suggested.", "muted"),
    table(["Value", "Rows", "Suggestion", "Meaning"], kindRows, "Record kinds"));
  wrap.append(el("h4", "Endpoints"), el("p", "Each PK endpoint becomes its own analyte. A code seen only on dose records is never one.", "muted"),
    table(["Value", "Rows by record kind", "Labels", "Units", "Meaning"], endRows, "Endpoints"));
  if (mdvRows.length) wrap.append(el("h4", "MDV values"), table(["Value", "Rows", "Meaning"], mdvRows, "MDV values"));
  if (needed.size) wrap.append(el("h4", "Choices the data needs"), choiceBox);
  const actions = el("div", null, "nca-row");
  actions.append(useSuggestions, save, remove);
  wrap.append(actions);
  return wrap;
}

async function put(state, events, status, message) {
  try {
    const data = await send("PUT", `api/nca/recipes/${encodeURIComponent(state.recipe.id)}/events`, { events });
    Object.assign(state, { recipe: data.recipe, sha256: data.sha256, columns: data.columns, problem: null });
    state.changed("recipe");
    setStatus(status, message);
  } catch (e) {
    setStatus(status, e.message, true);
  }
}

function report(help) {
  const box = el("div");
  const parts = help.report.partitions;
  const rows = Object.entries(PARTITION_TEXT).map(([k, label]) => [label, cell(String(parts[k]), "nca-num")]);
  rows.push(["Every record", cell(String(help.report.rows_in), "nca-num")]);
  box.append(el("h4", "Where every record went"), table(["Partition", "Records"], rows, "Event partitions"));
  const open = help.findings.filter((f) => f.level === "blocking");
  if (open.length) {
    const ul = el("ul");
    for (const f of open) {
      const lines = f.lines.slice(0, 20).join(", ") + (f.lines.length > 20 ? "…" : "");
      ul.append(el("li", `${f.message} Lines ${lines}. ${f.fix_hint}`));
    }
    box.append(el("h4", `Unresolved (${open.length})`), ul);
  }
  if (help.preview && help.preview.rows.length) {
    box.append(
      el("h4", "First prepared observations"),
      el("p", "Time after dose is the observation's actual time minus its dose's actual time.", "muted"),
      table(help.preview.columns, help.preview.rows, "First prepared observations"),
    );
  }
  return box;
}
