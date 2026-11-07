// Column mapping (#23): which column plays each role, the units, and what the values of
// the flag columns mean. Suggestions are shown beside each role; nothing is filled in
// until the user picks it. Units are never guessed.

import {
  button, cell, choice, el, field, getJSON, raw, section, send, setStatus, table,
} from "./common.js";

const NONE = "__none__";
const UNIT_ROLES = { ACTUAL_TIME: "time", CONC: "concentration", DOSE: "dose" };
const FLAG_ROLES = ["BLQ", "MISSING", "EXCLUDED"];

export function initMapping(stack, state) {
  const sec = section(stack, "nca-mapping", "4. Column mapping",
    "Choose the column for each role. Suggestions are only suggestions: use one, or pick another column.");
  let help = null;
  let draft = null; // the mapping being edited

  async function load() {
    sec.body.replaceChildren();
    help = null;
    if (!state.recipe) return setStatus(sec.status, "");
    try {
      help = await getJSON(`api/nca/recipes/${encodeURIComponent(state.recipe.id)}/mapping-help`);
    } catch (e) {
      return setStatus(sec.status, e.status === 404 ? "Apply the recipe to all rows to map its columns." : e.message, e.status !== 404);
    }
    draft = structuredClone(help.mapping);
    draft.roles ||= {};
    draft.units ||= {};
    draft.flags ||= {};
    render();
  }

  const columnNames = () => help.columns.map((c) => c.column);
  const valuesOf = (name) => help.columns.find((c) => c.column === name);

  function roleRow(role) {
    const suggestion = help.suggestions.find((s) => s.role === role.id);
    const question = help.needs_choice.find((q) => q.role === role.id);
    const select = choice([[NONE, "(not in this data)"], ...columnNames()]);
    select.setAttribute("aria-label", role.label);
    const current = draft.roles[role.id];
    select.value = current === null ? NONE : current || "";
    select.addEventListener("change", () => {
      draft.roles[role.id] = select.value === NONE ? null : select.value || undefined;
      if (draft.roles[role.id] === undefined) delete draft.roles[role.id];
      render();
    });
    const hint = el("td");
    if (suggestion) {
      const text = el("span", `Suggested: ${suggestion.column} (${Math.round(suggestion.confidence * 100)}%)`);
      text.title = suggestion.reasons.join(" ");
      hint.append(text);
      if (draft.roles[role.id] !== suggestion.column) {
        const use = button("Use", () => { draft.roles[role.id] = suggestion.column; render(); }, "btn btn-ghost btn-sm");
        use.setAttribute("aria-label", `Use ${suggestion.column} for ${role.label}`);
        hint.append(" ", use);
      }
      hint.append(el("p", suggestion.reasons.join(" "), "nca-args"));
    } else if (question) {
      hint.append(el("strong", "Your choice: ", "nca-warn"), question.message);
      const named = question.choices.filter((c) => c.confidence !== null);
      if (named.length) hint.append(el("p", `Candidates: ${named.map((c) => c.value).join(", ")}`, "nca-args"));
    }
    const label = el("td");
    label.append(role.label);
    if (role.required) label.append(el("span", " (required)", "muted"));
    const pick = el("td");
    pick.append(select);
    return [label, pick, hint];
  }

  function unitEditor(roleId) {
    const box = el("div", null, "nca-row");
    const unit = draft.units[roleId];
    const kind = choice([["column", "A unit column"], ["value", "One unit for every row"]]);
    kind.setAttribute("aria-label", `How the ${UNIT_ROLES[roleId]} unit is given`);
    kind.value = unit ? ("column" in unit ? "column" : "value") : "";
    box.append(kind);
    if (kind.value === "column") {
      const col = choice(columnNames(), unit.column);
      col.setAttribute("aria-label", `${UNIT_ROLES[roleId]} unit column`);
      col.addEventListener("change", () => {
        draft.units[roleId] = { column: col.value };
        const unitRole = roleId === "CONC" ? "CONC_UNIT" : roleId === "DOSE" ? "DOSE_UNIT" : null;
        if (unitRole) draft.roles[unitRole] = col.value;
        render();
      });
      box.append(col);
      const values = unit.column && valuesOf(unit.column);
      if (values) box.append(el("span", values.values.map((v) => `"${v.value}"`).join(", "), "nca-args"));
    } else if (kind.value === "value") {
      const input = el("input");
      input.value = unit.value || "";
      input.setAttribute("aria-label", `${UNIT_ROLES[roleId]} unit`);
      input.placeholder = roleId === "ACTUAL_TIME" ? "h" : roleId === "CONC" ? "ng/mL" : "mg";
      input.addEventListener("change", () => { draft.units[roleId] = { value: input.value }; });
      box.append(input);
    }
    kind.addEventListener("change", () => {
      if (kind.value === "column") draft.units[roleId] = { column: "" };
      else if (kind.value === "value") draft.units[roleId] = { value: "" };
      else delete draft.units[roleId];
      render();
    });
    const hint = help.unit_hints[roleId];
    if (hint) box.append(el("span", `The header says ${hint}: confirm it above.`, "nca-args"));
    return box;
  }

  function flagEditor(roleId) {
    const column = draft.roles[roleId];
    const values = valuesOf(column);
    const flag = (draft.flags[roleId] ||= { table: {} });
    flag.table ||= {};
    const box = el("div", null, "nca-pairs");
    box.append(el("p", `What each value of ${column} means:`));
    for (const v of values.values) {
      const row = el("div", null, "nca-row");
      const sel = choice([["true", "TRUE"], ["false", "FALSE"], ["", "(not listed)"]].slice(0, 2));
      sel.setAttribute("aria-label", `${column} value "${v.value}" means`);
      sel.value = v.value in flag.table ? String(flag.table[v.value]) : "";
      sel.addEventListener("change", () => {
        if (sel.value === "") delete flag.table[v.value];
        else flag.table[v.value] = sel.value === "true";
      });
      row.append(raw(v.value), el("span", `(${v.rows} rows) means`), sel);
      box.append(row);
    }
    if (values.too_many) box.append(el("p", "This column has too many different values to be a flag.", "nca-danger"));
    if (roleId === "BLQ") {
      const check = el("input");
      check.type = "checkbox";
      check.checked = flag.text_pattern === "^<";
      check.addEventListener("change", () => {
        if (check.checked) flag.text_pattern = "^<";
        else delete flag.text_pattern;
      });
      box.append(field("Also BLQ: concentration text starting with “<”", check));
    }
    if (roleId === "EXCLUDED" && !draft.roles.EXCLUSION_REASON) {
      const reason = el("input");
      reason.value = flag.reason || "";
      reason.addEventListener("change", () => {
        if (reason.value.trim()) flag.reason = reason.value;
        else delete flag.reason;
      });
      box.append(field("Reason for every excluded row (no reason column is mapped)", reason));
    }
    return box;
  }

  function render() {
    sec.body.replaceChildren();
    sec.body.append(table(["Role", "Column", "Suggestion"], help.roles.map(roleRow), "Column for each role"));

    const units = el("div", null, "nca-body");
    units.append(el("h3", "Units"));
    for (const roleId of Object.keys(UNIT_ROLES)) {
      if (!draft.roles[roleId]) continue;
      units.append(field(`Unit of ${UNIT_ROLES[roleId]}`, unitEditor(roleId)));
    }
    sec.body.append(units);

    for (const roleId of FLAG_ROLES) {
      if (draft.roles[roleId] && valuesOf(draft.roles[roleId])) {
        sec.body.append(el("h3", `${help.roles.find((r) => r.id === roleId).label}`), flagEditor(roleId));
      }
    }

    if (help.routes.length) {
      sec.body.append(el("h3", "Routes"));
      sec.body.append(table(["Value", "Rows", "opnca-r reads it as"], help.routes.map((r) => [
        cell(raw(r.value)), cell(String(r.rows), "nca-num"),
        r.route ? r.route : cell(r.value.trim() ? "Not recognised: add a “Replace values” step" : "Blank", "nca-danger"),
      ]), "Route values"));
    }

    const problems = el("ul", null, "nca-list");
    for (const p of help.problems) {
      const li = el("li", null, "nca-finding blocking");
      li.append(el("strong", "Blocking: "), p.message);
      problems.append(li);
    }
    if (help.problems.length) sec.body.append(el("h3", "Still to do"), problems);

    const actions = el("div", null, "nca-row");
    actions.append(button("Save mapping", save, "btn btn-primary btn-sm"));
    sec.body.append(actions);
  }

  async function save() {
    const mapping = structuredClone(draft);
    for (const [k, v] of Object.entries(mapping.units)) {
      if (!draft.roles[k] || ("column" in v && !v.column) || ("value" in v && !v.value)) delete mapping.units[k];
    }
    for (const k of Object.keys(mapping.flags)) {
      if (!draft.roles[k] || !Object.keys(mapping.flags[k].table || {}).length) delete mapping.flags[k];
    }
    try {
      const out = await send("PUT", `api/nca/recipes/${encodeURIComponent(state.recipe.id)}/mapping`, { mapping });
      state.recipe.mapping = out.mapping;
      state.sha256 = out.sha256;
      await load(); // refreshes route values and what still blocks
      setStatus(sec.status, out.problems.length ? "Saved. Some roles still need a choice." : "Mapping saved.");
      state.changed("mapping");
    } catch (e) {
      setStatus(sec.status, e.message, true);
    }
  }

  state.onChange((what) => {
    if (what === "cleaned" || what === "recipe") load();
  });
}
