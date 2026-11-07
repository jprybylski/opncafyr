// One analyte, its checks and the start of a run (#24, #25). Blocking findings keep
// "Start analysis" disabled; every warning needs its own acknowledgement, which is
// recorded with the run. The BLQ rules and method have no default.

import {
  button, choice, el, field, getJSON, post, section, setStatus, waitForJob,
} from "./common.js";

const BLQ_RULES = [
  ["exclude", "Exclude the sample"],
  ["zero", "Set to zero"],
  ["half-lloq", "Half the LLOQ"],
  ["lloq-over-sqrt2", "LLOQ / √2"],
];
const BLQ_METHODS = [["conventional", "Conventional (rules above)"], ["censored_lognormal", "Censored log-normal"]];
const RULE_NAMES = { blq_predose: "BLQ before the dose", blq_postdose: "BLQ after the dose", blq_terminal: "BLQ at the end of the profile" };
const LEVEL_WORD = { blocking: "Blocking", warning: "Warning", info: "Note" };

export function initChecks(stack, state) {
  const pick = section(stack, "nca-analyte", "5. Analyte and checks",
    "Choose exactly one analyte. Rows are matched on the exact text, so “METAB ” is not “METAB”.");
  const start = section(stack, "nca-start", "6. Start analysis",
    "Choose how BLQ samples are handled. There is no default: opnca-r uses exactly what you pick.");

  let analytes = null;
  let result = null;
  const acked = new Set();

  async function load() {
    pick.body.replaceChildren();
    start.body.replaceChildren();
    result = null;
    acked.clear();
    if (!state.recipe) return;
    try {
      analytes = await getJSON(`api/nca/recipes/${encodeURIComponent(state.recipe.id)}/analytes`);
    } catch (e) {
      return setStatus(pick.status, e.status === 404 || e.status === 409 ? "Apply the recipe and map the analyte column first." : e.message, e.status >= 500);
    }
    setStatus(pick.status, "");
    const analyteSel = choice(analytes.values.map((v) => [v.value, `"${v.value}" (${v.rows} rows)`]));
    const specimenSel = choice([]);
    const specimenField = field(`Specimen (${analytes.specimen_column || "not mapped"})`, specimenSel);
    specimenField.hidden = !analytes.specimen_column;
    analyteSel.addEventListener("change", () => {
      const v = analytes.values.find((x) => x.value === analyteSel.value);
      const specs = v ? Object.entries(v.specimens) : [];
      specimenSel.replaceChildren(...choice(specs.map(([s, n]) => [s, `"${s}" (${n} rows)`])).children);
      label.value = v ? (Object.entries(v.labels).sort((a, b) => b[1] - a[1])[0]?.[0] || v.value) : "";
      clearFindings();
    });
    specimenSel.addEventListener("change", clearFindings);
    const label = el("input");
    const row = el("div", null, "nca-row");
    row.append(field("Analyte (exact value)", analyteSel), specimenField,
      button("Run checks", () => runChecks(analyteSel.value, analytes.specimen_column ? specimenSel.value : null), "btn btn-primary btn-sm"));
    const findings = el("div", null, "nca-body");
    findings.id = "nca-findings";
    pick.body.append(row, findings);
    renderStart(label);
  }

  function clearFindings() {
    result = null;
    acked.clear();
    document.getElementById("nca-findings")?.replaceChildren();
    updateStart();
  }

  async function runChecks(analyte, specimen) {
    if (!analyte) return setStatus(pick.status, "Choose an analyte.", true);
    if (specimen === "") return setStatus(pick.status, "Choose a specimen.", true);
    setStatus(pick.status, "Checking…");
    try {
      const body = { analyte_value: analyte, specimen_value: specimen };
      result = await post(`api/nca/recipes/${encodeURIComponent(state.recipe.id)}/checks`, body);
      result.selection = body;
      acked.clear();
      renderFindings();
      setStatus(pick.status, `${result.summary.blocking} blocking, ${result.summary.warning} to acknowledge, ${result.summary.info} notes.`);
      if (result.format.job_id) {
        const job = await waitForJob(result.format.job_id);
        if (job.state === "completed") {
          const more = await post(`api/nca/recipes/${encodeURIComponent(state.recipe.id)}/format-check`, body);
          result.findings.push(...(more.findings || []));
          renderFindings();
        }
      }
    } catch (e) {
      setStatus(pick.status, e.message, true);
    }
  }

  function renderFindings() {
    const box = document.getElementById("nca-findings");
    box.replaceChildren();
    const list = el("ul", null, "nca-list");
    for (const f of result.findings) {
      const li = el("li", null, `nca-finding ${f.level}`);
      const head = el("div", null, "nca-row");
      head.append(el("strong", `${LEVEL_WORD[f.level]}: ${f.check.replaceAll("_", " ")}`));
      if (f.subject) head.append(el("span", `subject ${f.subject}`, "muted"));
      li.append(head, el("p", f.message));
      const detail = [];
      if (f.lines.length) detail.push(`lines ${f.lines.slice(0, 20).join(", ")}${f.lines.length > 20 ? "…" : ""}`);
      if (f.column) detail.push(`${f.column} = "${f.value}"`);
      if (f.values) detail.push(`values: ${f.values}`);
      if (f.kind) detail.push(f.kind);
      if (f.role) detail.push(f.role);
      if (detail.length) li.append(el("p", detail.join(" · "), "nca-args"));
      if (f.fix_hint) li.append(el("p", f.fix_hint, "nca-args"));
      if (f.level === "warning") {
        const box2 = el("input");
        box2.type = "checkbox";
        box2.checked = acked.has(f.key);
        box2.addEventListener("change", () => {
          if (box2.checked) acked.add(f.key); else acked.delete(f.key);
          updateStart();
        });
        li.append(field("I have reviewed this and want to go on", box2));
      }
      if (f.accounting) li.append(el("p", Object.entries(f.accounting).map(([k, v]) => `${k.replaceAll("_", " ")}: ${v}`).join(" · "), "nca-args"));
      list.append(li);
    }
    box.append(list);
    updateStart();
  }

  let startButton = null;
  let blockedNote = null;
  const rules = {};
  let method = null;
  let labelInput = null;

  function renderStart(label) {
    labelInput = label;
    const grid = el("div", null, "nca-row");
    for (const [name, text] of Object.entries(RULE_NAMES)) {
      const sel = choice(BLQ_RULES);
      sel.addEventListener("change", () => { rules[name] = sel.value; updateStart(); });
      grid.append(field(text, sel));
    }
    method = choice(BLQ_METHODS);
    method.addEventListener("change", updateStart);
    grid.append(field("BLQ method", method));
    start.body.append(field("Label for this analyte (shown in reports)", label), grid);
    blockedNote = el("p", null, "status");
    startButton = button("Start analysis", startRun, "btn btn-primary");
    start.body.append(blockedNote, startButton);
    updateStart();
  }

  function why() {
    if (!result) return "Run the checks first.";
    if (result.summary.blocking) return `${result.summary.blocking} blocking findings: change the recipe or mapping.`;
    const open = result.findings.filter((f) => f.level === "warning" && !acked.has(f.key)).length;
    if (open) return `${open} warnings still to acknowledge.`;
    if (Object.keys(RULE_NAMES).some((k) => !rules[k]) || !method?.value) return "Choose all three BLQ rules and the BLQ method.";
    return "";
  }

  function updateStart() {
    if (!startButton) return;
    const reason = why();
    startButton.disabled = Boolean(reason);
    setStatus(blockedNote, reason);
  }

  async function startRun() {
    try {
      const { run } = await post("api/nca/runs", {
        recipe_id: state.recipe.id,
        ...result.selection,
        analyte_label: labelInput.value || null,
        acknowledged: [...acked],
        blq_rules: { ...rules },
        blq_method: method.value,
      });
      setStatus(start.status, `Run ${run.run_id} created.`);
      state.changed("runs");
    } catch (e) {
      setStatus(start.status, e.message, true);
    }
  }

  state.onChange((what) => {
    if (what === "mapping" || what === "cleaned" || what === "recipe") load();
  });
}
