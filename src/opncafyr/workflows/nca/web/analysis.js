// The Analysis page of the NCA pack: source -> cleaning -> mapping -> one analyte -> run.
// Every step is explicit: nothing is chosen for the user, and suggestions say so.

import { el, on } from "./common.js";
import { initCleaning } from "./cleaning.js";
import { initMapping } from "./mapping.js";

// Page state shared by the sections. `changed(what)` tells the others to refresh.
export const state = {
  recipe: null, // the recipe document as saved
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
  on("workspace-changed", () => state.changed("workspace"));
}
