// Runs of this study (#25-#29): one analyte each, with their state. Actions on a run are
// added by the later steps of the path (session, save, replay, completion).

import { button, cell, el, formatTime, getJSON, post, raw, section, setStatus, stateBadge, table } from "./common.js";

export const runActions = []; // (run, refresh) => [Node], filled by other modules

export function initRuns(stack, state) {
  const sec = section(stack, "nca-runs", "7. Runs",
    "Each run holds one analyte's frozen input. A run never changes once it is completed: a change makes a new run.");

  async function refresh() {
    let data;
    try {
      data = await getJSON("api/nca/runs");
    } catch (e) {
      return setStatus(sec.status, e.message, true);
    }
    sec.body.replaceChildren();
    if (!data.runs.length) {
      sec.body.append(el("p", "No runs yet.", "muted"));
      return;
    }
    const rows = data.runs.map((run) => {
      const actions = el("td");
      for (const make of runActions) for (const node of make(run, refresh)) actions.append(node, " ");
      return [
        cell(el("span", run.run_id, "path")),
        cell(run.analyte ? raw(run.analyte.value) : ""),
        run.analyte?.label || "",
        cell(stateBadge(run.state)),
        formatTime(run.created_at),
        actions,
      ];
    });
    sec.body.append(table(["Run", "Analyte", "Label", "State", "Created", ""], rows, "Runs"));
  }

  // Integrity check and "new run from this one" (#26): a run never changes once made.
  runActions.push((run) => {
    if (run.state === "incomplete") return [];
    const check = button("Check files", async () => {
      try {
        const { report } = await getJSON(`api/runs/${encodeURIComponent(run.run_id)}/verify`);
        const issues = [...report.changed.map((p) => `changed ${p}`), ...report.missing.map((p) => `missing ${p}`), ...report.added.map((p) => `added ${p}`)];
        setStatus(sec.status, report.ok ? `${run.run_id}: every file matches its recorded hash.` : `${run.run_id}: ${issues.join(", ")}`, !report.ok);
      } catch (e) { setStatus(sec.status, e.message, true); }
    }, "btn btn-ghost btn-sm");
    check.setAttribute("aria-label", `Check the files of run ${run.run_id}`);
    const nodes = [check];
    if (run.state === "completed" || run.state === "failed") {
      const again = button("New run from this", async () => {
        try {
          const { run: made } = await post(`api/nca/runs/${encodeURIComponent(run.run_id)}/rerun`);
          setStatus(sec.status, `Run ${made.run_id} created from ${run.run_id}.`);
          refresh();
        } catch (e) { setStatus(sec.status, e.message, true); }
      }, "btn btn-ghost btn-sm");
      again.setAttribute("aria-label", `New run from ${run.run_id}`);
      nodes.push(again);
    }
    return nodes;
  });

  state.refreshRuns = refresh;
  state.onChange((what) => {
    if (what === "runs" || what === "workspace") refresh();
  });
  refresh();
}
