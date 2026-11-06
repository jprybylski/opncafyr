// Runs and publications in the open study folder (read-only, /api/workspace/artifacts).
// A run without a valid manifest shows as "Incomplete", never as finished.

import { getJSON } from "./api.js";
import { $, el, on, pollWhileVisible, setStatus, stateBadge, timeCell } from "./dom.js";

let lastSignature = "";

function analyteText(analyte) {
  if (!analyte) return "";
  const name = analyte.label || analyte.value;
  return `${name} (${analyte.column})`;
}

function cells(...items) {
  const tr = el("tr");
  for (const item of items) {
    if (item instanceof Node && item.nodeName === "TD") tr.append(item);
    else if (item instanceof Node) { const td = el("td"); td.append(item); tr.append(td); }
    else tr.append(el("td", item ?? ""));
  }
  return tr;
}

function note(id, text) {
  $(id).textContent = text;
  $(id).hidden = !text;
}

// Run and publication counts under the study folder name.
function facts(runs, pubs) {
  const ul = $("ws-facts");
  const fact = (n, one, many) => {
    const li = el("li");
    li.append(el("strong", String(n)), ` ${n === 1 ? one : many}`);
    return li;
  };
  ul.replaceChildren(fact(runs, "run", "runs"), fact(pubs, "publication", "publications"));
  ul.hidden = false;
}

function render(data) {
  const runsTable = $("runs-table");
  const pubsTable = $("pubs-table");
  if (!data.workspace_open) {
    note("runs-note", "Open a study folder to see its runs.");
    note("pubs-note", "Open a study folder to see its publications.");
    runsTable.hidden = true;
    pubsTable.hidden = true;
    $("ws-facts").hidden = true;
    return;
  }
  const runs = data.runs;
  note("runs-note", runs.length ? "" : "No runs yet in this study folder.");
  $("runs-body").replaceChildren(
    ...runs.map((r) => cells(el("span", r.run_id, "path"), analyteText(r.analyte), stateBadge(r.state), timeCell(r.created_at))),
  );
  runsTable.hidden = !runs.length;

  const pubs = data.publications;
  note("pubs-note", pubs.length ? "" : "No publications yet. Publish a completed run to make a report or deck.");
  $("pubs-body").replaceChildren(
    ...pubs.map((p) => cells(el("span", p.publication_id, "path"), p.items === undefined ? "" : String(p.items), stateBadge(p.state), timeCell(p.created_at))),
  );
  pubsTable.hidden = !pubs.length;
  facts(runs.length, pubs.length);
}

async function refresh() {
  try {
    const data = await getJSON("api/workspace/artifacts");
    const signature = JSON.stringify(data);
    if (signature === lastSignature) return;
    lastSignature = signature;
    render(data);
    setStatus($("runs-status"), "");
  } catch (e) {
    setStatus($("runs-status"), "Could not load the runs.", true);
  }
}

export function initArtifacts() {
  const poller = pollWhileVisible(refresh, 15000);
  on("workspace-changed", () => poller.refresh());
  on("job-finished", () => poller.refresh());
}
