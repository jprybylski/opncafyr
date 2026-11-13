// Study folder: native picker, folder check, create/open/upgrade, recent folders (#17).

import { getJSON, post } from "./api.js";
import { $, el, emit, setStatus } from "./dom.js";
import { notifyGuidance, registerCondition } from "./guidance.js";

const ACTIONS = {
  new: "Create workspace here",
  incomplete: "Create workspace here",
  workspace: "Open workspace",
  needs_migration: "Upgrade (backup kept) and open",
};

const status = (text, isError) => setStatus($("ws-status"), text, isError);

// The study card on Home and the chip in the top bar.
function showCurrent(ws) {
  const box = $("ws-current");
  const chip = $("study-chip-text");
  const pick = $("ws-pick");
  if (!ws) {
    box.replaceChildren(
      el("span", "No study folder is open", "study-title"),
      el("span", "Choose a folder to keep this study's data, runs and reports together.", "lede"),
    );
    chip.replaceChildren("No study folder open");
    chip.className = "chip-text none";
    pick.textContent = "Choose study folder…";
    pick.className = "btn btn-primary";
    return;
  }
  box.replaceChildren(el("span", ws.name, "study-title"), el("span", ws.path, "path"));
  chip.replaceChildren(el("strong", ws.name), " ", el("span", ws.path, "path"));
  chip.className = "chip-text";
  pick.textContent = "Change study folder…";
  pick.className = "btn";
}

async function activate(folder) {
  const creating = folder.state === "new" || folder.state === "incomplete";
  const res = creating
    ? await post("api/workspace/init", { path: folder.path })
    : await post("api/workspace/open", { path: folder.path, migrate: folder.state === "needs_migration" });
  let msg = `Opened ${res.workspace.name}.`;
  if (res.migrated_from) {
    msg += ` Upgraded from schema ${res.migrated_from}; the old file is in ${res.backups.join(", ")}.`;
  }
  if (res.swept_jobs.length) {
    msg += ` ${res.swept_jobs.length} unfinished job(s) from an earlier session were marked failed.`;
  }
  return msg;
}

function showFolder(folder) {
  const box = $("ws-folder");
  box.replaceChildren();
  box.hidden = false;
  const dl = el("dl", null, "kv");
  const row = (k, v, cls) => dl.append(el("dt", k), el("dd", v, cls));
  row("Folder", folder.path, "path");
  row("Can write here", folder.writable ? "Yes" : "No");
  row("Items in folder", String(folder.entries));
  if (folder.schema_version) row("Workspace format", `schema ${folder.schema_version}`);
  box.append(dl, el("p", folder.message));
  const label = ACTIONS[folder.state];
  if (!label) return;
  const button = el("button", label, "btn btn-primary");
  button.type = "button";
  button.addEventListener("click", async () => {
    button.disabled = true;
    try {
      const msg = await activate(folder);
      box.hidden = true;
      status(msg);
      await refresh();
      emit("workspace-changed");
      $("ws-pick").focus();
    } catch (e) {
      status(e.message, true);
      button.disabled = false;
    }
  });
  box.append(button);
  button.focus();
}

function recentItem(entry) {
  const li = el("li");
  const button = el("button", entry.name, "linkish");
  button.type = "button";
  button.disabled = !entry.available;
  button.addEventListener("click", async () => {
    try {
      showFolder((await post("api/workspace/inspect", { path: entry.path })).folder);
      status("");
    } catch (e) {
      status(e.message, true);
    }
  });
  li.append(button, " ", el("span", entry.available ? entry.path : `${entry.path} (not found)`, "path"));
  return li;
}

let isOpen = false; // for the guidance condition shell.workspace.open

async function refresh() {
  const d = await getJSON("api/workspace");
  showCurrent(d.workspace);
  isOpen = Boolean(d.workspace);
  notifyGuidance();
  const ul = $("ws-recent");
  if (!d.recent.length) ul.replaceChildren(el("li", "None yet.", "muted"));
  else ul.replaceChildren(...d.recent.map(recentItem));
}

export function initWorkspace() {
  registerCondition("shell.workspace.open", () => isOpen);
  $("ws-pick").addEventListener("click", async () => {
    const button = $("ws-pick");
    button.disabled = true;
    status("Waiting for the folder picker…");
    try {
      const res = await post("api/workspace/pick");
      if (res.cancelled) {
        status("No folder chosen.");
        return;
      }
      status("");
      showFolder(res.folder);
    } catch (e) {
      status(e.message, true);
    } finally {
      button.disabled = false;
    }
  });
  refresh().catch(() => status("Could not load the workspace state.", true));
}
