// The Publish page of the NCA pack: completed runs -> OUTPUTS/ -> report and deck.
// Publishing is a separate, reviewable step; nothing here changes a run.

import { el, on } from "./common.js";
import { initCurator, initPublications } from "./curator.js";
import { initOutputs } from "./outputs.js";
import { initReport } from "./report.js";
import { initDeck } from "./deck.js";

export const state = {
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
  const h1 = el("h1", "Publish");
  h1.id = "publish-heading";
  h1.tabIndex = -1;
  head.append(
    h1,
    el(
      "p",
      "Copy tables and figures from completed runs into OUTPUTS/ with their metadata, " +
        "then build a report or deck from them. A successful build shows the files were " +
        "assembled; it does not validate the analysis.",
    ),
  );
  const stack = el("div", null, "stack");
  container.append(head, stack);
  initCurator(stack, state);
  initPublications(stack, state);
  initOutputs(stack, state);
  initReport(stack, state);
  initDeck(stack, state);
  on("workspace-changed", () => state.changed("workspace"));
  // Runs completed on the Analysis page show up when this page is opened again.
  on("view-changed", (route) => {
    if (route.name === "publish") state.changed("shown");
  });
}
