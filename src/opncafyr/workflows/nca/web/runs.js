// Runs of this study (#25-#29): one analyte each, with their state. Actions on a run are
// added by the later steps of the path (session, save, replay, completion).

import { button, cell, el, formatTime, getJSON, post, raw, section, setStatus, stateBadge, table } from "./common.js";
import { emit, pollWhileVisible } from "../../js/dom.js";

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
    open = data.runs.filter((r) => r.state === "running" || r.state === "reviewing").map((r) => r.run_id);
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

  // Open a run in opnca-r (#27): one session at a time, on the run's frozen input.
  runActions.push((run) => {
    if (run.state !== "preparing" && run.state !== "running") return [];
    const open = button("Open in opnca-r", async () => {
      open.disabled = true;
      setStatus(sec.status, `Starting opnca-r on run ${run.run_id}…`);
      try {
        await post(`api/nca/runs/${encodeURIComponent(run.run_id)}/session`);
        setStatus(sec.status, `opnca-r is open on run ${run.run_id}. Save there when you are done.`);
        emit("app-restarted", "opnca");
        location.hash = "#/apps/opnca";
      } catch (e) {
        setStatus(sec.status, e.message, true);
      } finally {
        open.disabled = false;
        refresh();
      }
    }, "btn btn-primary btn-sm");
    open.setAttribute("aria-label", `Open run ${run.run_id} in opnca-r`);
    return [open];
  });

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

  // Saving happens in opnca-r (its Export view); the launcher notices it on disk (#28).
  runActions.push((run) => {
    if (run.state !== "reviewing") return [];
    const done = button("Confirm and complete", async () => {
      try {
        await post(`api/nca/runs/${encodeURIComponent(run.run_id)}/complete`);
        setStatus(sec.status, `Run ${run.run_id} is completed and sealed.`);
      } catch (e) { setStatus(sec.status, e.message, true); }
      refresh();
    }, "btn btn-primary btn-sm");
    done.setAttribute("aria-label", `Confirm and complete run ${run.run_id}`);
    const again = button("Replay again", async () => {
      try {
        await post(`api/nca/runs/${encodeURIComponent(run.run_id)}/replay`, {});
        if (!open.includes(run.run_id)) open.push(run.run_id);
        setStatus(sec.status, `Replaying run ${run.run_id}…`);
      } catch (e) { setStatus(sec.status, e.message, true); }
    }, "btn btn-ghost btn-sm");
    again.setAttribute("aria-label", `Replay run ${run.run_id} again`);
    return [done, again];
  });

  let open = [];
  async function watchSaves() {
    for (const runId of open) {
      try {
        const out = await post(`api/nca/runs/${encodeURIComponent(runId)}/save-status`);
        if (out.save.state === "saving") setStatus(sec.status, `opnca-r is saving run ${runId}…`);
        if (out.run.state === "reviewing") {
          const replay = out.replay || {};
          if (replay.job && !["completed", "failed", "cancelled"].includes(replay.job.state)) {
            setStatus(sec.status, `Replaying run ${runId}: ${replay.job.message}`);
          } else if (replay.job && replay.job.state === "failed") {
            setStatus(sec.status, `Run ${runId}: ${replay.job.message}`, true);
            open = open.filter((id) => id !== runId);
          } else if (replay.status) {
            setStatus(sec.status, replay.status === "passed"
              ? `Run ${runId} replayed and matches its saved results. Confirm it to complete the run.`
              : `Run ${runId} did not replay to the same results:\n${replay.differences}`, replay.status !== "passed");
            open = open.filter((id) => id !== runId);
            refresh();
          }
          continue;
        }
        if (out.run.state !== "running" && out.run.state !== "preparing") {
          setStatus(sec.status, out.save.state === "complete"
            ? `Run ${runId} was saved by opnca-r and is ready for review.`
            : `Run ${runId}: the save failed. ${out.save.problems.join(" ")}`, out.save.state !== "complete");
          refresh();
        }
      } catch (e) { /* the next poll tries again */ }
    }
  }
  pollWhileVisible(watchSaves, 3000);

  state.refreshRuns = refresh;
  state.onChange((what) => {
    if (what === "runs" || what === "workspace") refresh();
  });
  refresh();
}
