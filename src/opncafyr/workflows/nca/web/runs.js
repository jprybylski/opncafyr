// Runs of this study (#25-#29): one analyte each, with their state. Actions on a run are
// added by the later steps of the path (session, save, replay, completion).

import { cell, el, formatTime, getJSON, raw, section, setStatus, stateBadge, table } from "./common.js";

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

  state.refreshRuns = refresh;
  state.onChange((what) => {
    if (what === "runs" || what === "workspace") refresh();
  });
  refresh();
}
