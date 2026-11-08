// Version history (#56): optional Git snapshots of the study folder, in gitneighbr.
// Nothing here runs Git. Opening the page starts nothing; the user opens version
// history, and turns sending on for that session only if they want it.

import { el, getJSON, on, post, send, setStatus } from "./common.js";
import { button, section } from "./common.js";

const EXPLAIN =
  "A snapshot is an optional, local copy of files you choose, kept with Git. It is not a " +
  "run, a publication or a build: those stay fixed, hash-checked records of their own, and " +
  "a snapshot never changes, approves or validates them. The study folder works fully " +
  "without version history.";

const SENSITIVE =
  "Study data, results, reports, decks and their history can contain identifiable or " +
  "sensitive information. Keep snapshots on this computer unless you are allowed to share " +
  "what they contain. Sending is off unless you turn it on, and every file is listed " +
  "before anything leaves.";

export function init(container) {
  const head = el("div", null, "page-head");
  const h1 = el("h1", "Version history");
  h1.id = "history-heading";
  h1.tabIndex = -1;
  head.append(h1, el("p", EXPLAIN));
  const stack = el("div", null, "stack");
  container.append(head, stack);

  const sec = section(stack, "history-card", "Snapshots of this study folder", SENSITIVE);
  const remote = el("input");
  remote.type = "checkbox";
  remote.id = "history-remote";
  const remoteLabel = el("label", " Also allow sending to, and getting updates from, a shared copy (this session only)");
  remoteLabel.htmlFor = remote.id;
  remoteLabel.prepend(remote);
  const actions = el("div", null, "actions");
  sec.body.append(remoteLabel, actions);

  async function act(fn) {
    try {
      await fn();
    } catch (err) {
      setStatus(sec.status, err.message || String(err), "error");
    }
    await refresh();
  }

  async function refresh() {
    actions.replaceChildren();
    let state;
    try {
      state = await getJSON("api/nca/history");
    } catch (err) {
      setStatus(sec.status, err.message || String(err), "error");
      return;
    }
    remote.disabled = state.running;
    remoteLabel.hidden = !state.available;
    if (!state.workspace_open) {
      setStatus(sec.status, "Open a study folder first.");
      return;
    }
    if (!state.installed || !state.available) {
      setStatus(
        sec.status,
        `Version history is unavailable${state.detail ? `: ${state.detail}` : "."} ` +
          "Everything else in the app works without it.",
      );
      return;
    }
    if (state.running) {
      remote.checked = state.remote;
      setStatus(sec.status, state.remote ? "Open, with sending allowed." : "Open, on this computer only.");
      actions.append(
        button("Show version history", () => {
          location.hash = "#/apps/gitneighbr";
        }, "btn"),
        button("Close version history", () => act(async () => {
          await send("DELETE", "api/nca/history");
        })),
      );
    } else {
      setStatus(sec.status, "");
      actions.append(
        button("Open version history", () => act(async () => {
          setStatus(sec.status, "Starting version history…");
          await post("api/nca/history", { remote: remote.checked });
          location.hash = "#/apps/gitneighbr";
        }), "btn"),
      );
    }
  }

  refresh();
  on("workspace-changed", refresh);
  on("view-changed", (route) => {
    if (route.name === "history") refresh();
  });
}
