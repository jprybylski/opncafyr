// Views in one page (#50): "#/" Home, "#/data", "#/jobs", "#/settings", and "#/apps/<id>" for a
// tool's web app embedded in the launcher. Other hashes, such as the skip link's
// "#main", leave the current view alone.

import { emit } from "./dom.js";

const VIEWS = { "": "home", data: "data", jobs: "jobs", settings: "settings" };

let current = null; // "home", "data", "jobs", "settings" or "tool:<id>"
let currentRoute = { name: "home" };

export const route = () => currentRoute;

function routeFromHash() {
  const hash = location.hash;
  if (hash === "" || hash === "#") return { name: "home" };
  if (!hash.startsWith("#/")) return null;
  const path = hash.slice(2);
  if (path.startsWith("apps/")) {
    let id = path.slice(5).replace(/\/+$/, "");
    try { id = decodeURIComponent(id); } catch (e) { /* keep the raw text */ }
    if (id) return { name: "tool", param: id };
  }
  return { name: VIEWS[path] || "home" };
}

function show(route, moveFocus) {
  const key = route.param === undefined ? route.name : `${route.name}:${route.param}`;
  if (key === current) return;
  current = key;
  currentRoute = route;
  for (const view of document.querySelectorAll("main .view")) {
    view.hidden = view.id !== `view-${route.name}`;
  }
  document.body.classList.toggle("in-tool", route.name === "tool");
  for (const link of document.querySelectorAll(".sidenav .nav-link")) {
    if (link.dataset.view === route.name) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  }
  const view = document.getElementById(`view-${route.name}`);
  document.title = `${view.dataset.title} · opncafyr`;
  emit("view-changed", route); // the tool view fills in its heading and title
  // After a navigation, start reading at the new view's heading.
  if (moveFocus) view.querySelector("h1")?.focus({ preventScroll: false });
}

export function initRouter() {
  show(routeFromHash() || { name: "home" }, false);
  window.addEventListener("hashchange", () => {
    const route = routeFromHash();
    if (route) show(route, true);
  });
}
