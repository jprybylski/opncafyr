// "Study profile step (optional)" for Start analysis and Publish (#68). Shown only while
// the study follows a profile with steps of this kind. It starts at "None": a run or
// publication records a profile step only when the user picks one, and that changes
// nothing in the analysis.

import { choice, el, field, getJSON, on } from "./common.js";

export function profileStepField(kind, hint) {
  const select = choice([], "", "None");
  const wrap = field("Study profile step (optional)", select, hint);
  wrap.hidden = true;
  const note = el("p", null, "hint");
  wrap.append(note);

  async function refresh() {
    let current = null;
    try {
      current = (await getJSON("api/nca/profiles")).current;
    } catch (e) {
      current = null;
    }
    const usable = current && current.profile && !current.changed && !(current.problems || []).length;
    const steps = usable ? current.profile.steps.filter((s) => s.type === kind) : [];
    const keep = select.value;
    select.replaceChildren(...choice(steps.map((s) => [s.id, s.title]), "", "None").children);
    select.value = steps.some((s) => s.id === keep) ? keep : "";
    wrap.hidden = !(current && current.selected);
    select.disabled = !steps.length;
    note.textContent = !current || !current.selected ? ""
      : current.changed ? "The profile file changed: adopt it on the Study profile page to record a step."
      : !steps.length ? "The profile has no step of this kind." : "";
  }

  on("profile-changed", refresh);
  on("workspace-changed", refresh);
  on("view-changed", (route) => { if (route.name === "analysis" || route.name === "publish") refresh(); });
  refresh();
  return { node: wrap, value: () => select.value || null };
}
