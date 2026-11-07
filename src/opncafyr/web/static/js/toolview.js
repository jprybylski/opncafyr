// A tool's web app embedded in the launcher (#50, option A): "#/apps/<id>".
// The launcher's top bar, notice, jobs and Quit stay in view. Each app gets one
// same-origin iframe, created on first visit and kept while other views are shown, so
// unsaved work in a tool survives a trip to Home. The sidebar lists every web app.
// Nothing here names a tool: ids, names and routes come from /api/tools and /api/apps.

import { $, appBadge, appStateWord, el, on, relativeRoute } from "./dom.js";
import { route } from "./router.js";

let apps = null; // id -> { id, name, route, state }, null until the first poll
const frames = new Map(); // id -> iframe

const SHOW_FRAME = new Set(["running", "crashed"]); // crashed: the proxy page offers Restart

function currentId() {
  const r = route();
  return r.name === "tool" ? r.param : null;
}

function navItem(app) {
  const link = el("a", null, "nav-link nav-app");
  link.href = `#/apps/${encodeURIComponent(app.id)}`;
  link.dataset.app = app.id;
  link.title = `${app.name}: ${appStateWord(app.state)}`;
  const mono = el("span", app.name.charAt(0), "monogram");
  mono.setAttribute("aria-hidden", "true");
  const dot = el("span", null, `app-dot app-${app.state}`);
  mono.append(dot);
  link.append(mono, el("span", app.name, "nav-label"), el("span", `, ${appStateWord(app.state)}`, "visually-hidden"));
  if (app.id === currentId()) link.setAttribute("aria-current", "page");
  const li = el("li");
  li.append(link);
  return li;
}

function renderNav() {
  const list = [...apps.values()];
  const ul = $("nav-apps");
  ul.replaceChildren(...list.map(navItem));
  ul.hidden = !list.length;
  $("nav-apps-heading").hidden = !list.length;
}

function frameFor(app) {
  let frame = frames.get(app.id);
  if (!frame) {
    frame = el("iframe", null, "tool-frame");
    frame.title = app.name;
    frame.src = relativeRoute(app.route);
    frame.dataset.app = app.id;
    frames.set(app.id, frame);
    $("tool-frames").append(frame);
  }
  return frame;
}

function note(text) {
  $("tool-note").textContent = text;
  $("tool-note").hidden = !text;
}

function renderView() {
  const id = currentId();
  if (id === null) return;
  const app = apps ? apps.get(id) : undefined;
  const name = app ? app.name : id;
  $("tool-heading").textContent = name;
  $("tool-mono").textContent = name.charAt(0);
  document.title = `${name} · opncafyr`;
  $("tool-state").replaceChildren(app ? appBadge(app.state) : "");
  const newTab = $("tool-newtab");
  newTab.hidden = !app;
  if (app) newTab.href = relativeRoute(app.route);

  let shown = null;
  if (!apps) note("Loading…");
  else if (!app) note(`No installed tool has a web app called "${id}".`);
  else if (SHOW_FRAME.has(app.state) || frames.has(app.id)) {
    note("");
    shown = frameFor(app);
  } else if (app.state === "starting") note(`${name} is starting. It opens here when it is ready.`);
  else note(`${name} is not running.`);
  for (const frame of frames.values()) frame.hidden = frame !== shown;
  $("tool-reload").disabled = !shown;
}

function reload() {
  const frame = frames.get(currentId());
  if (!frame) return;
  try {
    frame.contentWindow.location.reload();
  } catch (e) {
    frame.src = frame.src; // eslint-disable-line no-self-assign
  }
}

export function initToolView() {
  $("tool-reload").addEventListener("click", reload);
  // A module that restarted an app's session (a workflow opening another run) asks for a
  // fresh page, so the frame never shows the previous session.
  on("app-restarted", (id) => {
    const frame = frames.get(id);
    if (frame) frame.src = frame.src; // eslint-disable-line no-self-assign
  });
  on("apps-changed", (list) => {
    apps = new Map(list.map((a) => [a.id, a]));
    // An app that went away loses its frame.
    for (const [id, frame] of frames) {
      if (!apps.has(id)) { frame.remove(); frames.delete(id); }
    }
    renderNav();
    renderView();
  });
  on("view-changed", () => {
    if (apps) renderNav();
    renderView();
  });
  renderView();
}
