// Tool tiles. Everything shown comes from /api/tools and /api/apps: this file never
// names a tool, so a new adapter appears here without a change to core files.

import { getJSON } from "./api.js";
import { $, el, pollWhileVisible, setStatus, stateBadge } from "./dom.js";

const APP_STATES = {
  not_started: "Not running.",
  starting: "Starting…",
  running: "Running.",
  crashed: "Stopped unexpectedly.",
  stopped: "Stopped.",
};

const humanize = (capability) => capability.replace(/_/g, " ");

// Routes from the API are absolute paths; keep links relative to the page (ADR 0006).
const relative = (route) => route.replace(/^\/+/, "");

function tile(tool, app) {
  const li = el("li", null, "tile");
  li.append(el("h3", tool.display_name || tool.id));
  if (tool.health) {
    const p = el("p");
    p.append(stateBadge(tool.health.state));
    if (tool.health.state !== "ok") p.append(` ${tool.health.detail}`);
    li.append(p);
  }
  for (const req of tool.missing_requirements || []) li.append(el("p", req.message, "muted"));
  if (tool.capabilities.length) {
    li.append(el("p", `Can: ${tool.capabilities.map(humanize).join(", ")}.`, "muted"));
  }
  if (app) {
    li.append(el("p", `Web app: ${APP_STATES[app.state] || app.state}`));
    if (app.state === "running") {
      const link = el("a", `Open ${tool.display_name || tool.id}`);
      link.href = relative(app.route);
      const p = el("p");
      p.append(link);
      li.append(p);
    }
  }
  return li;
}

let lastSignature = "";

async function refresh() {
  try {
    const [tools, apps] = await Promise.all([getJSON("api/tools"), getJSON("api/apps")]);
    const signature = JSON.stringify([tools, apps]);
    if (signature === lastSignature) return;
    lastSignature = signature;
    const byId = new Map(apps.apps.map((a) => [a.id, a]));
    const ul = $("tools");
    const focused = document.activeElement && ul.contains(document.activeElement)
      ? document.activeElement.textContent : null;
    const known = new Set(tools.tools.map((t) => t.id));
    // An app with no descriptor entry still gets a tile, named by its id.
    const extra = apps.apps.filter((a) => !known.has(a.id)).map((a) => ({ id: a.id, capabilities: [] }));
    const all = [...tools.tools, ...extra];
    if (!all.length) ul.replaceChildren(el("li", "No tools are installed."));
    else ul.replaceChildren(...all.map((t) => tile(t, byId.get(t.id))));
    if (focused) [...ul.querySelectorAll("a")].find((a) => a.textContent === focused)?.focus();
    setStatus($("tools-status"), "");
  } catch (e) {
    setStatus($("tools-status"), "Could not load the installed tools.", true);
  }
}

export function initTools() {
  pollWhileVisible(refresh, 10000);
}
