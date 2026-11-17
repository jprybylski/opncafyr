// Guidance contracts on the page (ADR 0010; tutorials #69, study profiles #68).
//
// - A target is an element marked data-target="<id>". Ids come from /api/guidance; core
//   and each workflow pack declare their own.
// - A condition is a read-only milestone a page reports from its own in-memory state. A
//   page registers a function; guidance only asks whether it holds. A condition function
//   must not fetch, post or change anything.
// - notifyGuidance() tells whoever is waiting on a condition to look again.
// - A target or condition whose catalog entry has "app" lives inside that embedded app
//   (ADR 0013): the app outlines or answers it itself, through framebridge.js.

import { getJSON } from "./api.js";
import { emit } from "./dom.js";
import { appClear, appCondition, appHighlight, appShown } from "./framebridge.js";

const conditions = new Map();
let catalog = null;
const appOf = new Map(); // target or condition id -> the app it lives in

export function markTarget(node, id) {
  node.dataset.target = id;
  return node;
}

export function registerCondition(id, fn) {
  conditions.set(id, fn);
}

// true, false, or undefined when no page reports this condition (yet). A condition an
// embedded app reports is asked of the app; its answer arrives as "guidance-changed".
export function checkCondition(id) {
  const fn = conditions.get(id);
  if (!fn) {
    const app = appOf.get(id);
    return app ? appCondition(app, id) : undefined;
  }
  try {
    return Boolean(fn());
  } catch (e) {
    return undefined;
  }
}

export function notifyGuidance() {
  emit("guidance-changed");
}

export async function guidanceCatalog() {
  if (!catalog) {
    catalog = await getJSON("api/guidance");
    for (const item of [...catalog.targets, ...catalog.conditions]) {
      if (item.app) appOf.set(item.id, item.app);
    }
  }
  return catalog;
}

// The embedded app a target lives in, or null for a launcher control.
export const targetApp = (id) => appOf.get(id) || null;

// An embedded app's display name, from the catalog.
export function appName(app) {
  const found = catalog && catalog.apps.find((a) => a.id === app);
  return found ? found.title : app;
}

const isShown = (node) => node.isConnected && !node.closest("[hidden]") && node.getClientRects().length > 0;

// The element for a target id if it is on the page and visible, else null.
export function findTarget(id) {
  for (const node of document.querySelectorAll("[data-target]")) {
    if (node.dataset.target === id && isShown(node)) return node;
  }
  return null;
}

// The hash for a view id ("home" is "#/").
export const viewHash = (view) => (view === "home" ? "#/" : `#/${view}`);

// Show a target: switch to its view, scroll to it and move focus there. Returns the
// element, or null if it isn't on the page (a state or version where it is absent).
export async function goToTarget(id) {
  const known = (await guidanceCatalog()).targets.find((t) => t.id === id);
  if (!known) return null;
  if (known.app) return goToAppTarget(known);
  if (known.view && location.hash !== viewHash(known.view)) {
    location.hash = viewHash(known.view);
    // The router shows the view (and focuses its heading) on hashchange: wait for that, so
    // the focus below is the last word.
    const view = document.getElementById(`view-${known.view}`);
    for (let i = 0; i < 60 && view && view.hidden; i++) await nextFrame();
    await nextFrame();
  }
  const node = findTarget(id);
  if (!node) return null;
  node.scrollIntoView({ block: "center" });
  focusInto(node);
  return node;
}

const nextFrame = () => new Promise((r) => requestAnimationFrame(r));
const APP_OUTLINE_MS = 5000;

// A control inside an embedded app: show the app and ask it to outline the control for a
// few seconds. Focus stays where it is: the launcher never moves focus inside an app.
async function goToAppTarget(known) {
  const hash = `#/apps/${encodeURIComponent(known.app)}`;
  if (location.hash !== hash) location.hash = hash;
  for (let i = 0; i < 60 && !appShown(known.app); i++) await nextFrame();
  const state = await appHighlight(known.app, known.id);
  if (state !== "shown") return null;
  setTimeout(() => appClear(known.app), APP_OUTLINE_MS);
  return document.querySelector(`iframe[data-app="${CSS.escape(known.app)}"]`);
}

const FOCUSABLE ="a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]";

// Focus the target itself if it takes focus, else its heading or first control.
export function focusInto(node) {
  if (node.matches(FOCUSABLE)) return node.focus({ preventScroll: true });
  const inner = node.querySelector("h1, h2, h3") || node.querySelector(FOCUSABLE);
  if (inner) {
    if (!inner.matches(FOCUSABLE)) inner.tabIndex = -1;
    inner.focus({ preventScroll: true });
  }
}
