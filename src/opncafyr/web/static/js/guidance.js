// Guidance contracts on the page (ADR 0010; tutorials #69, study profiles #68).
//
// - A target is an element marked data-target="<id>". Ids come from /api/guidance; core
//   and each workflow pack declare their own.
// - A condition is a read-only milestone a page reports from its own in-memory state. A
//   page registers a function; guidance only asks whether it holds. A condition function
//   must not fetch, post or change anything.
// - notifyGuidance() tells whoever is waiting on a condition to look again.

import { getJSON } from "./api.js";
import { emit } from "./dom.js";

const conditions = new Map();
let catalog = null;

export function markTarget(node, id) {
  node.dataset.target = id;
  return node;
}

export function registerCondition(id, fn) {
  conditions.set(id, fn);
}

// true, false, or undefined when no page reports this condition (yet).
export function checkCondition(id) {
  const fn = conditions.get(id);
  if (!fn) return undefined;
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
  if (!catalog) catalog = await getJSON("api/guidance");
  return catalog;
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
