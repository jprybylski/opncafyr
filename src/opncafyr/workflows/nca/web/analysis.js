// The Analysis page of the NCA pack: source -> cleaning -> mapping -> one analyte -> run.
// Every step is explicit: nothing is chosen for the user, and suggestions say so.

import { el, notifyGuidance, on, registerCondition } from "./common.js";
import { initCleaning } from "./cleaning.js";
import { initMapping } from "./mapping.js";
import { initChecks } from "./checks.js";
import { initRuns } from "./runs.js";

// Page state shared by the sections. `changed(what)` tells the others to refresh.
export const state = {
  recipe: null, // the recipe document as saved
  cleanedNow: false, // the recipe as saved has a full result (guidance)
  mappingReady: false, // the saved mapping blocks nothing (mapping.js, guidance)
  runs: [], // as last listed (runs.js, guidance)
  sha256: null,
  columns: [], // columns after the recipe's steps
  header: [],
  problem: null,
  listeners: [],
  changed(what) {
    for (const fn of this.listeners) fn(what);
  },
  onChange(fn) {
    this.listeners.push(fn);
  },
};

export function init(container) {
  const link = document.createElement("link");
  link.rel = "stylesheet";
  link.href = "static/workflows/nca/analysis.css";
  document.head.append(link);

  const head = el("div", null, "page-head");
  const h1 = el("h1", "Analysis");
  h1.id = "analysis-heading";
  h1.tabIndex = -1;
  head.append(
    h1,
    el(
      "p",
      "Clean an imported file, map its columns, choose one analyte and start a run in opnca-r. " +
        "The source file is never changed.",
    ),
  );
  const stack = el("div", null, "stack");
  container.append(head, stack);
  initCleaning(stack, state);
  initMapping(stack, state);
  initChecks(stack, state);
  initRuns(stack, state);
  on("workspace-changed", () => state.changed("workspace"));
  registerConditions();
}

// Read-only milestones for tutorials and study profiles (ADR 0010), declared in
// __init__.py. Each reads this page's state; none fetches or changes anything.
function registerConditions() {
  state.onChange((what) => {
    // A changed recipe has no full result until "cleaned" says so (cleaning.js).
    if (what === "recipe" || what === "workspace") state.cleanedNow = false;
    if (what === "cleaned") state.cleanedNow = true;
    notifyGuidance();
  });
  registerCondition("nca.recipe.chosen", () => Boolean(state.recipe));
  registerCondition("nca.cleaned.ready", () => Boolean(state.recipe) && state.cleanedNow);
  registerCondition("nca.mapping.ready", () => Boolean(state.recipe) && state.mappingReady);
  registerCondition("nca.checks.ready", () => Boolean(
    state.checks && !state.checks.stale && state.checks.summary && state.checks.summary.blocking === 0));
  registerCondition("nca.run.created", () => state.runs.length > 0);
  registerCondition("nca.run.completed", () => state.runs.some((r) => r.state === "completed"));
}
