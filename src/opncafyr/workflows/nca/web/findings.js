// Findings of the analyte checks (#24), drawn in two places: in full, with the warning
// acknowledgements, under "5. Analyte and checks"; and as a pinned, read-only copy under the
// cleaning steps (#63), so a person fixing findings doesn't scroll between the two.
//
// The pinned copy never holds an acknowledgement: acknowledging stays in section 5, tied to
// the recipe hash the checks ran against. Findings from an earlier recipe or mapping are
// never shown as current: `state.checks.stale` hides them and says to check again.

import { button, el, field } from "./common.js";

export const LEVEL_WORD = { blocking: "Blocking", warning: "Warning", info: "Note" };

// One finding. `ack` is { checked, onChange } for the full list, or null for the pinned copy.
export function findingItem(f, ack) {
  const li = el("li", null, `nca-finding ${f.level}`);
  const head = el("div", null, "nca-row");
  head.append(el("strong", `${LEVEL_WORD[f.level]}: ${f.check.replaceAll("_", " ")}`));
  if (f.subject) head.append(el("span", `subject ${f.subject}`, "muted"));
  li.append(head, el("p", f.message));
  const detail = [];
  if (f.lines.length) detail.push(`lines ${f.lines.slice(0, 20).join(", ")}${f.lines.length > 20 ? "…" : ""}`);
  if (f.column) detail.push(`${f.column} = "${f.value}"`);
  if (f.values) detail.push(`values: ${f.values}`);
  if (f.kind) detail.push(f.kind);
  if (f.role) detail.push(f.role);
  if (detail.length) li.append(el("p", detail.join(" · "), "nca-args"));
  if (f.fix_hint) li.append(el("p", f.fix_hint, "nca-args"));
  if (ack && f.level === "warning") {
    const box = el("input");
    box.type = "checkbox";
    box.checked = ack.checked;
    box.addEventListener("change", () => ack.onChange(box.checked));
    li.append(field("I have reviewed this and want to go on", box));
  }
  if (f.accounting) li.append(el("p", Object.entries(f.accounting).map(([k, v]) => `${k.replaceAll("_", " ")}: ${v}`).join(" · "), "nca-args"));
  return li;
}

const selectionText = (sel) =>
  sel ? `“${sel.analyte_value}”${sel.specimen_value ? `, specimen “${sel.specimen_value}”` : ""}` : "";

// The pinned panel. `card` is the cleaning-steps section and `before` the node it goes
// above (the section's status line), so it sits directly below the step adder.
export function initPinned(card, before, state) {
  const panel = el("details", null, "nca-pinned");
  panel.hidden = true;
  const summary = el("summary");
  const body = el("div", null, "nca-pinned-body");
  // Said aloud when findings go out of date; the list itself is not a live region.
  const alert = el("p", null, "visually-hidden");
  alert.setAttribute("role", "status");
  panel.append(summary, body);
  card.insertBefore(panel, before);
  card.insertBefore(alert, before);
  let wasStale = false;

  const toFull = () => {
    const target = document.getElementById("nca-analyte");
    if (!target) return;
    target.scrollIntoView({ block: "start" });
    const heading = target.querySelector("h2");
    if (heading) { heading.tabIndex = -1; heading.focus(); }
  };

  function render() {
    const c = state.checks;
    panel.hidden = !c;
    if (!c) { wasStale = false; return; }
    summary.replaceChildren();
    summary.append(el("strong", "Findings"), ` for ${selectionText(c.selection)} `);
    body.replaceChildren();
    if (c.stale) {
      summary.append(el("span", "Out of date", "pill pill-warn pill-stale"));
      body.append(el("p", "These findings are from an earlier version of the recipe or mapping, so they are hidden. " +
        "Apply the recipe to all rows, then run the checks again.", "card-note"));
      if (!wasStale) alert.textContent = "The findings are out of date. Apply the recipe, then run the checks again.";
    } else {
      alert.textContent = "";
      const s = c.summary;
      summary.append(`: ${s.blocking} blocking, ${s.warning} to acknowledge · checked at ${c.checkedAt}`);
      const shown = c.findings.filter((f) => f.level !== "info");
      if (!shown.length) body.append(el("p", "No blocking findings or warnings.", "muted"));
      else {
        const list = el("ul", null, "nca-list");
        for (const f of shown) list.append(findingItem(f, null));
        body.append(list);
      }
      if (s.info) body.append(el("p", `${s.info} notes are in the full list.`, "muted"));
    }
    body.append(button("Back to the full findings and acknowledgements", toFull, "btn btn-ghost btn-sm"));
    wasStale = Boolean(c.stale);
  }

  state.onChange((what) => { if (what === "checks") render(); });
  render();
}
