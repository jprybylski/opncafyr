// Editors in this page that hold unsaved text (#52). The lifecycle sends their tool ids
// with every heartbeat, so idle shutdown waits and Quit asks; the page can save them all
// before quitting. Nothing here knows what an editor edits.

import { emit } from "./dom.js";

const editors = new Map(); // key -> { tool, dirty(), save() }

// Register an editor; returns a function that removes it.
export function register(key, tool, dirty, save) {
  editors.set(key, { tool, dirty, save });
  return () => {
    editors.delete(key);
    changed();
  };
}

// Tool ids with unsaved text, sorted and unique.
export function unsavedTools() {
  const tools = new Set();
  for (const e of editors.values()) if (e.dirty()) tools.add(e.tool);
  return [...tools].sort();
}

export const anyUnsaved = (tool) => unsavedTools().some((t) => tool === undefined || t === tool);

// Save every dirty editor. Resolves to true when all of them saved.
export async function saveAll() {
  let ok = true;
  for (const e of editors.values()) {
    if (!e.dirty()) continue;
    try {
      if (!(await e.save())) ok = false;
    } catch (err) {
      ok = false;
    }
  }
  changed();
  return ok;
}

// Call whenever an editor's dirty state may have changed.
export function changed() {
  emit("unsaved-changed", unsavedTools());
}

window.addEventListener("beforeunload", (ev) => {
  if (!unsavedTools().length) return;
  ev.preventDefault();
  ev.returnValue = "";
});
