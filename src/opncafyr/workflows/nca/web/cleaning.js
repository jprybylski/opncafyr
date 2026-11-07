// Cleaning (#22): pick an imported source, edit its recipe step by step, preview the
// result on the first rows and apply it to every row. Each step shows rows in, rows
// out and rows rejected; rejected rows are listed with the step and reason.

import {
  button, cell, choice, el, field, getJSON, post, raw, section, send, setStatus, table,
  waitForJob,
} from "./common.js";

// One entry per step kind the user can add. "derive" has three forms in the UI.
const OPS = {
  clean_names: { label: "Clean column names", fields: [] },
  rename: { label: "Rename a column", fields: [["from", "Column", "column"], ["to", "New name", "text"]] },
  trim: { label: "Trim spaces", fields: [["columns", "Columns", "columns"]] },
  parse_type: {
    label: "Check numbers",
    fields: [
      ["column", "Column", "column"],
      ["type", "Kind of number", "choice", [["number", "Number"], ["integer", "Whole number"]]],
      ["decimal_mark", "Decimal mark", "choice", [[".", "Point (1.5)"], [",", "Comma (1,5)"]]],
    ],
  },
  recode: { label: "Replace values", fields: [["column", "Column", "column"], ["map", "Replace", "pairs"]] },
  derive_copy: {
    op: "derive", fn: "copy", label: "Copy a column",
    fields: [["target", "New column", "text"], ["from", "From column", "column"]],
  },
  derive_concat: {
    op: "derive", fn: "concat", label: "Join columns",
    fields: [["target", "New column", "text"], ["from", "Columns", "columns"], ["sep", "Separator", "text"]],
  },
  derive_constant: {
    op: "derive", fn: "constant", label: "Add a constant column",
    fields: [["target", "New column", "text"], ["value", "Value", "text"]],
  },
  derive_elapsed_time: {
    label: "Derive elapsed time",
    fields: [
      ["target", "New column", "text"],
      ["from", "Sample date-time", "column"],
      ["reference", "Reference date-time (dose)", "column"],
      ["unit", "Unit", "choice", [["h", "hours"], ["min", "minutes"], ["d", "days"], ["s", "seconds"], ["w", "weeks"]]],
      ["time_zone", "Time zones (in words)", "text"],
    ],
  },
  convert_units: {
    label: "Convert units",
    fields: [
      ["value_column", "Value column", "column"],
      ["unit_column", "Unit column", "column"],
      ["from", "From unit (exact text)", "text"],
      ["to", "To unit", "text"],
    ],
  },
  flag: {
    label: "Make a TRUE/FALSE column",
    fields: [["target", "New column", "text"], ["column", "From column", "column"], ["table", "Values", "bools"]],
  },
  exclude: {
    label: "Exclude rows (they stay, marked)",
    fields: [["where", "Rows", "where"], ["reason", "Reason", "text"]],
  },
  filter_rows: {
    label: "Remove rows to the rejected-row log",
    fields: [["where", "Rows", "where"], ["reason", "Reason", "text"]],
  },
  report_dupes: { label: "Report duplicate rows", fields: [["columns", "Columns", "columns"]] },
};

const opLabel = (step) => {
  if (step.op === "derive") return OPS[`derive_${step.args.fn}`].label;
  return OPS[step.op]?.label || step.op;
};

function describeWhere(w) {
  if (w.all) return w.all.map(describeWhere).join(" and ");
  if ("equals" in w) return `${w.column} is "${w.equals}"`;
  if ("in" in w) return `${w.column} is one of ${w.in.map((v) => `"${v}"`).join(", ")}`;
  return `${w.column} is blank`;
}

function describe(step) {
  const a = step.args;
  switch (step.op) {
    case "rename": return `${a.from} → ${a.to}`;
    case "trim": case "report_dupes": return a.columns.join(", ");
    case "parse_type": return `${a.column} (${a.type}, decimal mark "${a.decimal_mark}")`;
    case "recode": return `${a.column}: ` + Object.entries(a.map).map(([f, t]) => `"${f}" → "${t}"`).join(", ");
    case "derive":
      if (a.fn === "copy") return `${a.target} = ${a.from}`;
      if (a.fn === "concat") return `${a.target} = ${a.from.join(` "${a.sep}" `)}`;
      return `${a.target} = "${a.value}"`;
    case "derive_elapsed_time": return `${a.target} = ${a.from} − ${a.reference} in ${a.unit}; time zones: ${a.time_zone}`;
    case "convert_units": return `${a.value_column} where ${a.unit_column} is "${a.from}" → ${a.to}`;
    case "flag": return `${a.target} from ${a.column}: ` + Object.entries(a.table).map(([v, f]) => `"${v}" → ${f}`).join(", ");
    case "exclude": case "filter_rows": return `${describeWhere(a.where)}. Reason: ${a.reason}`;
    default: return "";
  }
}

// --- Small editors ------------------------------------------------------------------

function columnSelect(columns, multiple) {
  const s = multiple ? el("select") : choice(columns);
  if (multiple) {
    s.multiple = true;
    s.size = Math.min(8, Math.max(3, columns.length));
    for (const c of columns) { const o = el("option", c); o.value = c; s.append(o); }
  }
  return s;
}

// Rows of exact-text inputs. Spaces are kept: "METAB " and "METAB" are different values.
function pairsEditor(kind) {
  const box = el("div", null, "nca-pairs");
  const rows = el("div", null, "nca-pairs");
  const add = () => {
    const row = el("div", null, "nca-row");
    const from = el("input");
    from.setAttribute("aria-label", kind === "bools" ? "Value (exact text)" : "Replace (exact text)");
    let to;
    if (kind === "bools") {
      to = choice([["true", "TRUE"], ["false", "FALSE"]]);
      to.setAttribute("aria-label", "Means");
    } else {
      to = el("input");
      to.setAttribute("aria-label", "With");
    }
    row.append(from, el("span", kind === "bools" ? "means" : "→"), to, button("Remove", () => row.remove(), "btn btn-ghost btn-sm"));
    rows.append(row);
  };
  add();
  box.append(rows, button("Add a value", add, "btn btn-ghost btn-sm"));
  box.read = () => {
    const out = {};
    for (const row of rows.children) {
      const [from, , to] = row.children;
      if (from.value === "" && to.value === "" && kind !== "bools") continue;
      if (from.value in out) throw new Error(`"${from.value}" is listed twice.`);
      if (kind === "bools") {
        if (!to.value) throw new Error(`Choose TRUE or FALSE for "${from.value}".`);
        out[from.value] = to.value === "true";
      } else {
        out[from.value] = to.value;
      }
    }
    if (!Object.keys(out).length) throw new Error("Add at least one value.");
    return out;
  };
  return box;
}

function whereEditor(columns) {
  const box = el("div", null, "nca-row");
  const col = choice(["opncafyr_line", ...columns.filter((c) => c !== "opncafyr_line")]);
  col.setAttribute("aria-label", "Column");
  const how = choice([["equals", "is"], ["in", "is one of (one per line)"], ["is_blank", "is blank"]]);
  how.setAttribute("aria-label", "Condition");
  const values = el("textarea");
  values.setAttribute("aria-label", "Value or values, exact text");
  box.append(col, how, values);
  box.read = () => {
    if (!col.value) throw new Error("Choose the column the rows are picked by.");
    if (!how.value) throw new Error("Choose a condition.");
    if (how.value === "is_blank") return { column: col.value, is_blank: true };
    const lines = values.value.split("\n");
    if (how.value === "equals") return { column: col.value, equals: values.value.replace(/\n$/, "") };
    const list = lines.filter((l) => l !== "");
    if (!list.length) throw new Error("List at least one value.");
    return { column: col.value, in: list };
  };
  return box;
}

function stepForm(kind, columns, onAdd, onCancel) {
  const spec = OPS[kind];
  const form = el("div", null, "nca-form");
  form.append(el("strong", spec.label));
  const readers = {};
  for (const [name, label, type, options] of spec.fields) {
    let control;
    if (type === "column") control = columnSelect(columns, false);
    else if (type === "columns") control = columnSelect(columns, true);
    else if (type === "choice") control = choice(options);
    else if (type === "pairs" || type === "bools") control = pairsEditor(type);
    else if (type === "where") control = whereEditor(columns);
    else control = el("input");
    form.append(field(label, control));
    readers[name] = () => {
      if (control.read) return control.read();
      if (type === "columns") {
        const picked = [...control.selectedOptions].map((o) => o.value);
        if (!picked.length) throw new Error(`Choose ${label.toLowerCase()}.`);
        return picked;
      }
      if (control.value === "" && name !== "sep" && name !== "value") throw new Error(`Fill in ${label.toLowerCase()}.`);
      return control.value;
    };
  }
  const note = el("input");
  form.append(field("Note (optional)", note, "Why this step is needed. Kept with the recipe."));
  const status = el("p", null, "status");
  const actions = el("div", null, "nca-row");
  actions.append(
    button("Add step", () => {
      try {
        const args = {};
        if (spec.fn) args.fn = spec.fn;
        for (const [name, read] of Object.entries(readers)) args[name] = read();
        const step = { op: spec.op || kind, args };
        if (note.value.trim()) step.note = note.value;
        onAdd(step, (message) => setStatus(status, message, true));
      } catch (e) {
        setStatus(status, e.message, true);
      }
    }, "btn btn-primary btn-sm"),
    button("Cancel", onCancel, "btn btn-ghost btn-sm"),
  );
  form.append(actions, status);
  return form;
}

// --- The section --------------------------------------------------------------------

export function initCleaning(stack, state) {
  const pick = section(stack, "nca-recipe", "1. Source and recipe",
    "Choose an imported file. Its recipe lists the cleaning steps in order.");
  const steps = section(stack, "nca-steps", "2. Cleaning steps",
    "Steps run in this order. Nothing is removed unless a step says so, with a reason.");
  const result = section(stack, "nca-clean-result", "3. Result",
    "Preview runs the steps on the first 200 rows; Apply runs them on every row.");

  const sourceSel = choice([]);
  const recipeSel = choice([]);
  const sourceField = field("Imported file", sourceSel);
  const recipeField = field("Recipe", recipeSel);
  const newRecipe = button("New recipe", async () => {
    if (!sourceSel.value) return setStatus(pick.status, "Choose a file first.", true);
    try {
      const out = await post("api/nca/recipes", { source_id: sourceSel.value });
      await refreshRecipes(out.id);
    } catch (e) { setStatus(pick.status, e.message, true); }
  });
  const row = el("div", null, "nca-row");
  row.append(sourceField, recipeField, newRecipe);
  pick.body.append(row);

  let sources = [];
  let recipes = [];

  async function refreshSources() {
    const data = await getJSON("api/nca/sources");
    sources = data.sources;
    const keep = sourceSel.value;
    sourceSel.replaceChildren(...choice(sources.map((s) => [s.id, s.path])).children);
    sourceSel.value = sources.some((s) => s.id === keep) ? keep : "";
    if (!data.workspace_open) setStatus(pick.status, "Open a study folder first.");
    else if (!sources.length) setStatus(pick.status, "Import a file on the Data page first.");
    else setStatus(pick.status, "");
  }

  async function refreshRecipes(select) {
    const data = await getJSON("api/nca/recipes");
    recipes = data.recipes.filter((r) => !sourceSel.value || r.source_id === sourceSel.value);
    recipeSel.replaceChildren(...choice(recipes.map((r) => [r.id, `${r.id} (${r.steps} steps)`])).children);
    if (select) recipeSel.value = select;
    await loadRecipe(recipeSel.value);
  }

  async function loadRecipe(id) {
    if (!id) {
      Object.assign(state, { recipe: null, sha256: null, columns: [], header: [], problem: null });
      state.changed("recipe");
      return render();
    }
    const data = await getJSON(`api/nca/recipes/${encodeURIComponent(id)}`);
    Object.assign(state, {
      recipe: data.recipe, sha256: data.sha256, columns: data.columns || [],
      header: data.header || [], problem: data.problem,
    });
    state.changed("recipe");
    render();
    await showResult("full", true);
  }

  async function save(recipe, report) {
    try {
      const data = await send("PUT", `api/nca/recipes/${encodeURIComponent(recipe.id)}`, { recipe });
      Object.assign(state, { recipe: data.recipe, sha256: data.sha256, columns: data.columns, problem: null });
      const option = recipeSel.selectedOptions[0];
      if (option && option.value === data.id) option.textContent = `${data.id} (${data.steps} steps)`;
      state.changed("recipe");
      render();
      result.body.replaceChildren();
      setStatus(result.status, "The recipe changed. Preview or apply it again.");
      return true;
    } catch (e) {
      (report || ((m) => setStatus(steps.status, m, true)))(e.message);
      return false;
    }
  }

  function render() {
    steps.body.replaceChildren();
    const recipe = state.recipe;
    if (!recipe) {
      steps.body.append(el("p", "Choose or make a recipe.", "muted"));
      return;
    }
    if (state.problem) setStatus(steps.status, state.problem.message, true);
    else setStatus(steps.status, "");
    const list = el("ol", null, "nca-steps");
    recipe.steps.forEach((step, i) => {
      const li = el("li");
      const headRow = el("div", null, "nca-step-head");
      headRow.append(el("strong", opLabel(step)));
      const actions = el("span", null, "actions");
      const move = (delta) => {
        const copy = structuredClone(recipe);
        const [s] = copy.steps.splice(i, 1);
        copy.steps.splice(i + delta, 0, s);
        save(copy);
      };
      const up = button("Up", () => move(-1), "btn btn-ghost btn-sm");
      up.disabled = i === 0;
      up.setAttribute("aria-label", `Move step ${i + 1} up`);
      const down = button("Down", () => move(1), "btn btn-ghost btn-sm");
      down.disabled = i === recipe.steps.length - 1;
      down.setAttribute("aria-label", `Move step ${i + 1} down`);
      const remove = button("Remove", () => {
        const copy = structuredClone(recipe);
        copy.steps.splice(i, 1);
        save(copy);
      }, "btn btn-ghost btn-sm");
      remove.setAttribute("aria-label", `Remove step ${i + 1}`);
      actions.append(up, down, remove);
      headRow.append(actions);
      li.append(headRow, el("p", describe(step), "nca-args"));
      if (step.note) li.append(el("p", `Note: ${step.note}`, "nca-args"));
      list.append(li);
    });
    if (recipe.steps.length) steps.body.append(list);
    else steps.body.append(el("p", "No steps yet: the data is used as it is.", "muted"));

    const kind = choice(Object.entries(OPS).map(([k, v]) => [k, v.label]), "", "Add a step…");
    kind.setAttribute("aria-label", "Add a step");
    const slot = el("div");
    kind.addEventListener("change", () => {
      slot.replaceChildren();
      if (!kind.value) return;
      slot.append(stepForm(kind.value, state.columns, async (step, report) => {
        const copy = structuredClone(recipe);
        copy.steps.push(step);
        if (await save(copy, report)) kind.value = "";
      }, () => { kind.value = ""; slot.replaceChildren(); }));
    });
    const actions = el("div", null, "nca-row");
    actions.append(
      kind,
      button("Preview", () => run("preview"), "btn btn-sm"),
      button("Apply to all rows", () => run("full"), "btn btn-primary btn-sm"),
    );
    steps.body.append(actions, slot);
  }

  async function run(kind) {
    if (!state.recipe) return;
    const url = `api/nca/recipes/${encodeURIComponent(state.recipe.id)}/${kind === "preview" ? "preview" : "apply"}`;
    try {
      const started = await post(url);
      if (started.job_id) {
        const job = await waitForJob(started.job_id, (m) => setStatus(result.status, m));
        if (job.state !== "completed") return setStatus(result.status, job.message, true);
      }
      await showResult(kind, false);
    } catch (e) {
      setStatus(result.status, e.message, true);
    }
  }

  async function showResult(kind, quiet) {
    result.body.replaceChildren();
    if (!state.recipe) return;
    let data;
    try {
      data = await getJSON(`api/nca/recipes/${encodeURIComponent(state.recipe.id)}/result?kind=${kind}`);
    } catch (e) {
      if (!quiet) setStatus(result.status, e.message, true);
      else setStatus(result.status, "Not applied yet.");
      return;
    }
    const s = data.summary;
    state.cleaned = kind === "full" ? data : state.cleaned;
    if (kind === "full") state.changed("cleaned");
    setStatus(result.status, kind === "preview"
      ? `Preview of the first ${s.source_rows} rows: ${s.rows_out} kept, ${s.rows_rejected} rejected.`
      : `All ${s.source_rows} rows: ${s.rows_out} kept, ${s.rows_rejected} rejected.`);
    const rows = s.steps.map((st) => [
      String(st.index), opLabel({ op: st.op, args: state.recipe.steps[st.index - 1]?.args || {} }),
      cell(String(st.rows_in), "nca-num"), cell(String(st.rows_out), "nca-num"),
      cell(String(st.rows_rejected), "nca-num"), cell(String(st.cells_changed), "nca-num"),
      [...(st.columns_added || [])].join(", "),
      issuesText(st),
    ]);
    result.body.append(
      el("h3", "Rows at each step"),
      table(["Step", "What", "Rows in", "Rows out", "Rejected", "Values changed", "New columns", "Reported"], rows, "Rows at each step"),
    );
    if (data.rejected.length) {
      result.body.append(
        el("h3", `Rejected rows (${data.rejected.length})`),
        table(["Line", "Step", "Reason"], data.rejected.map((r) => [r.line, r.step, r.reason]), "Rejected rows"),
      );
    }
    const head = data.head.map((r) => r.map((v) => cell(raw(v))));
    result.body.append(el("h3", "First rows after cleaning"), table(data.columns, head, "First rows after cleaning"));
  }

  function issuesText(st) {
    const parts = [];
    for (const issue of st.issues || []) parts.push(`${issue.kind.replaceAll("_", " ")}: lines ${[...issue.lines].slice(0, 12).join(", ")}${issue.lines.length > 12 ? "…" : ""}`);
    for (const group of st.dupes || []) parts.push(`same values on lines ${[...group].join(", ")}`);
    return parts.join("; ");
  }

  sourceSel.addEventListener("change", () => refreshRecipes());
  recipeSel.addEventListener("change", () => loadRecipe(recipeSel.value));
  state.onChange(async (what) => {
    if (what !== "workspace") return;
    try { await refreshSources(); await refreshRecipes(); } catch (e) { setStatus(pick.status, e.message, true); }
  });
  state.changed("workspace");
  render();
}
