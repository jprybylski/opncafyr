// Publication curator (#31): pick tables and figures from completed runs, check, publish.
// Only completed runs whose replay passed and whose files still match can be chosen; the
// server enforces this too. Published files are copies; runs never change.

import { button, el, field, formatTime, getJSON, post, raw, section, setStatus, table } from "./common.js";

const short = (sha) => (sha ? sha.slice(0, 12) : "");
const statusWord = { new: "New copy", unchanged: "Already published (same bytes)", conflict: "Name taken by different content" };

export function initCurator(stack, state) {
  const sec = section(stack, "nca-curator", "1. Choose tables and figures",
    "Pick from completed runs. You can combine analytes in one publication. Each file is copied into " +
      "OUTPUTS/ with a metadata sidecar, and the publication records the exact runs and hashes.");
  const picker = el("div", null, "nca-body");
  const unavailable = el("div");
  const label = el("input");
  label.maxLength = 200;
  const actions = el("div", null, "nca-row");
  const plan = el("div");
  sec.body.append(picker, unavailable, field("Label (optional)", label), actions, plan);

  let boxes = []; // [{input, run_id, artifact}]
  let checkedKey = null;
  const selection = () => boxes.filter((b) => b.input.checked).map((b) => ({ run_id: b.run_id, artifact: b.artifact }));
  const key = () => JSON.stringify(selection());

  const check = button("Check", async () => {
    const items = selection();
    if (!items.length) return setStatus(sec.status, "Choose at least one table or figure.", true);
    check.disabled = true;
    setStatus(sec.status, "Checking…");
    try {
      const out = await post("api/nca/publications", { items, dry_run: true });
      showPlan(out.plan);
      checkedKey = out.plan.blocking ? null : key();
      publish.disabled = checkedKey === null;
      setStatus(sec.status, out.plan.blocking
        ? "This selection can't be published as it is; see below."
        : `${items.length} file(s) ready to publish.`, out.plan.blocking);
    } catch (e) {
      setStatus(sec.status, e.message, true);
    } finally {
      check.disabled = false;
    }
  }, "btn btn-sm");
  const publish = button("Publish", async () => {
    if (key() !== checkedKey) return setStatus(sec.status, "The selection changed; check it again first.", true);
    publish.disabled = true;
    setStatus(sec.status, "Publishing…");
    try {
      const out = await post("api/nca/publications", { items: selection(), label: label.value.trim() || null });
      const pub = out.publication;
      setStatus(sec.status, `Recorded ${pub.publication_id}: ${pub.items.length} file(s) from ${pub.runs.length} run(s).`);
      plan.replaceChildren();
      checkedKey = null;
      state.changed("publications");
    } catch (e) {
      setStatus(sec.status, e.message, true);
    }
  }, "btn btn-primary btn-sm");
  publish.disabled = true;
  actions.append(check, publish);

  function showPlan(p) {
    plan.replaceChildren();
    plan.append(table(["File", "Run", "Shows subject IDs", "Status"], p.items.map((i) => [
      el("span", i.published_path, "path"), el("span", i.run_id, "path"), i.contains_subject_ids ? "Yes" : "No",
      el("span", statusWord[i.status] || i.status, i.status === "conflict" ? "nca-danger" : ""),
    ]), "What would be published"));
    if (p.report.findings.length) {
      plan.append(table(["Level", "Check", "File", "Finding"], p.report.findings.map((f) => [
        f.severity === "error" ? "Blocks" : "Warning", f.check, el("span", f.path || "", "path"), f.message,
      ]), "Sidecar findings"));
    }
  }

  async function refresh() {
    let data;
    try {
      data = await getJSON("api/nca/publish/runs");
    } catch (e) {
      return setStatus(sec.status, e.message, true);
    }
    boxes = [];
    checkedKey = null;
    publish.disabled = true;
    plan.replaceChildren();
    picker.replaceChildren();
    const ready = data.runs.filter((r) => r.publishable);
    const other = data.runs.filter((r) => !r.publishable && r.state !== "incomplete");
    if (!ready.length) picker.append(el("p", "No completed runs yet. Complete a run on the Analysis page first.", "muted"));
    for (const run of ready) picker.append(runBlock(run));
    if (other.length) {
      picker.append(table(["Run", "Analyte", "Why it can't be published"], other.map((r) => [
        el("span", r.run_id, "path"), r.analyte ? raw(r.analyte.value) : "", r.reason || "",
      ]), "Runs that can't be published"));
    }
    unavailable.replaceChildren(...data.unavailable.map((u) => el("p", `${u.title}: ${u.reason}`, "card-note")));
  }

  function runBlock(run) {
    const set = el("fieldset", null, "nca-form");
    const legend = el("legend");
    legend.append(raw(run.analyte.value), ` · run `, el("span", run.run_id, "path"), ` · ${formatTime(run.created_at)}`);
    set.append(legend, el("p", `Replay passed · manifest ${short(run.manifest_sha256)}`, "hint"));
    for (const a of run.artifacts) {
      const row = el("label", null, "nca-row");
      const input = el("input");
      input.type = "checkbox";
      input.checked = a.preselected;
      input.addEventListener("change", () => {
        publish.disabled = key() !== checkedKey;
      });
      boxes.push({ input, run_id: run.run_id, artifact: a.artifact });
      row.append(input, `${a.title} (${a.kind})`);
      if (a.contains_subject_ids) row.append(el("span", "shows subject IDs", "chip"));
      row.append(el("span", short(a.sha256), "path muted"));
      set.append(row);
    }
    return set;
  }

  state.onChange((what) => {
    if (what === "workspace" || what === "publications" || what === "shown") refresh();
  });
  refresh();
}

export function initPublications(stack, state) {
  const sec = section(stack, "nca-publications", "2. Publications",
    "Each publication is a fixed set of files from exact runs. Reports and decks are built from one publication.");

  async function refresh() {
    let data;
    try {
      data = await getJSON("api/nca/publications");
    } catch (e) {
      return setStatus(sec.status, e.message, true);
    }
    state.publications = data.publications.filter((p) => p.state === "recorded");
    sec.body.replaceChildren();
    if (!data.publications.length) {
      sec.body.append(el("p", "No publications yet.", "muted"));
    } else {
      sec.body.append(table(["Publication", "Label", "Analytes", "Files", "Created"], data.publications.map((p) => [
        el("span", p.publication_id, "path"), p.label || (p.state === "incomplete" ? "Incomplete" : ""),
        (p.runs || []).map((r) => r.analyte.label || r.analyte.value).join(", "), String(p.items ?? ""), formatTime(p.created_at),
      ]), "Publications"));
    }
    state.changed("publications-listed");
  }

  state.onChange((what) => {
    if (what === "workspace" || what === "publications" || what === "shown") refresh();
  });
  refresh();
}
