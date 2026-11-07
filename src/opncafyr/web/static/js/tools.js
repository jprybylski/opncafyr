// Tool tiles. Everything shown comes from /api/tools and /api/apps: this file never
// names a tool, so a new adapter appears here without a change to core files.

import { getJSON } from "./api.js";
import { $, el, emit, on, pollWhileVisible, setStatus, stateBadge } from "./dom.js";

const APP_STATES = {
  not_started: "Web app not running",
  starting: "Web app starting…",
  running: "Web app running",
  crashed: "Web app stopped unexpectedly",
  stopped: "Web app stopped",
};

const humanize = (capability) => capability.replace(/_/g, " ");

function card(tool, app) {
  const name = tool.display_name || tool.id;
  const li = el("li", null, "tool");

  const top = el("div", null, "tool-top");
  const mono = el("span", name.charAt(0), "monogram");
  mono.setAttribute("aria-hidden", "true");
  const titles = el("div");
  titles.append(el("h3", name), el("div", app ? APP_STATES[app.state] || app.state : "Command-line tool", "sub"));
  top.append(mono, titles);
  li.append(top);

  if (tool.capabilities.length) {
    const chips = el("ul", null, "chips");
    chips.setAttribute("aria-label", `What ${name} can do`);
    chips.append(...tool.capabilities.map((c) => el("li", humanize(c), "chip")));
    li.append(chips);
  }
  if (tool.health && tool.health.state !== "ok" && tool.health.detail) {
    li.append(el("p", tool.health.detail, "note"));
  }
  for (const req of tool.missing_requirements || []) li.append(el("p", req.message, "note"));

  const foot = el("div", null, "tool-foot");
  if (tool.health) foot.append(stateBadge(tool.health.state));
  if (app && app.state === "running") {
    // Opens inside the launcher (toolview.js); the tool strip offers a new tab.
    const link = el("a", `Open ${name}`, "btn btn-primary btn-sm");
    link.href = `#/apps/${encodeURIComponent(tool.id)}`;
    foot.append(link);
  }
  if (foot.childNodes.length) li.append(foot);
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
    emit(
      "apps-changed",
      apps.apps.map((a) => {
        const tool = all.find((t) => t.id === a.id);
        return { id: a.id, route: a.route, state: a.state, name: (tool && tool.display_name) || a.id };
      }),
    );
    if (!all.length) ul.replaceChildren(el("li", "No tools are installed.", "card-note"));
    else ul.replaceChildren(...all.map((t) => card(t, byId.get(t.id))));
    $("tools-count").textContent = all.length === 1 ? "1 installed" : `${all.length} installed`;
    if (focused) [...ul.querySelectorAll("a")].find((a) => a.textContent === focused)?.focus();
    setStatus($("tools-status"), "");
  } catch (e) {
    setStatus($("tools-status"), "Could not load the installed tools.", true);
  }
}

export function initTools() {
  pollWhileVisible(refresh, 10000);
  // A workflow that just (re)started an app's session wants its state shown now, not at
  // the next poll: the tool view waits for "running" before it shows the page.
  on("app-restarted", () => refresh());
}
