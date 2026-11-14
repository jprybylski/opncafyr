// The Study profile page of the NCA pack (#68, ADR 0010): follow a study team's journey
// through the Analysis and Publish steps. A profile is guidance. Nothing here starts,
// changes or publishes a run, and nothing is chosen for the user: every tick, slot and
// publication is the user's own choice, which the server checks.

import { button, choice, el, field, getJSON, post, section, send, setStatus, stateBadge } from "./common.js";
import { emit, on } from "../../js/dom.js";
import { checkCondition, goToTarget, guidanceCatalog } from "../../js/guidance.js";
import { openGuidanceForm } from "../../js/guidance-form.js";

let catalog = { targets: [], conditions: [] };

export function init(container) {
  const link = document.createElement("link");
  link.rel = "stylesheet";
  link.href = "static/workflows/nca/analysis.css";
  document.head.append(link);

  const head = el("div", null, "page-head");
  const h1 = el("h1", "Study profile");
  h1.id = "profile-heading";
  h1.tabIndex = -1;
  head.append(
    h1,
    el("p", "A study profile lists the steps your team agreed for this kind of study: what to " +
      "check, which runs to make and what to publish together. It is guidance only. It never " +
      "chooses an analyte, dose, unit, BLQ rule, exclusion or comparison, and finishing it " +
      "does not validate the analysis."),
  );
  const stack = el("div", null, "stack");
  const editorHost = el("div");
  editorHost.hidden = true;
  container.append(head, editorHost, stack);

  const chooser = section(stack, "nca-profile-choose", "Profile",
    "Profiles are files in the study's profiles/ folder. Start a new one from an example, " +
      "or edit one here with Edit. A profile you follow changes only when you adopt its new text.",
    "nca.profile.choose");
  const steps = section(stack, "nca-profile-steps", "Steps",
    "Tick a step when it is done. Run and publication steps are done when you choose a " +
      "completed run or a matching publication.", "nca.profile.steps");

  // The editor (#76) replaces the page while it is open. It saves one file in profiles/;
  // nothing else changes until the user adopts the edited profile.
  async function edit(open) {
    stack.hidden = true;
    try {
      await openGuidanceForm(editorHost, {
        api: "api/nca/profiles",
        open,
        unsavedKey: "study-profile",
        onClose: () => {
          stack.hidden = false;
          refresh();
          h1.focus();
        },
      });
    } catch (e) {
      editorHost.hidden = true;
      stack.hidden = false;
      setStatus(chooser.status, `The editor could not open: ${e.message}`, true);
    }
  }

  let data = null;
  let candidates = { runs: [], publications: [] };
  const hints = []; // [node, condition id] for the live hints

  async function refresh() {
    try {
      catalog = await guidanceCatalog();
      data = await getJSON("api/nca/profiles");
      candidates = data.current && data.current.profile
        ? await getJSON("api/nca/profiles/candidates")
        : { runs: [], publications: [] };
    } catch (e) {
      return setStatus(chooser.status, e.message, true);
    }
    renderChooser();
    renderSteps();
  }

  // After a change, keep focus on the control that made it.
  async function act(fn, focusKey) {
    try {
      const current = await fn();
      if (current && "selected" in current) data.current = current;
      setStatus(steps.status, "");
      emit("profile-changed");
      await refresh();
    } catch (e) {
      setStatus(steps.status, e.message, true);
      await refresh();
    }
    if (focusKey) container.querySelector(`[data-key="${CSS.escape(focusKey)}"]`)?.focus();
  }

  function renderChooser() {
    const body = chooser.body;
    body.replaceChildren();
    if (!data.workspace_open) {
      body.append(el("p", "Open a study folder first.", "muted"));
      return;
    }
    const valid = data.study.filter((p) => !p.problems.length);
    const sel = choice(valid.map((p) => [p.name, `${p.title} (${p.name}, version ${p.version})`]),
      data.current?.selected || "");
    const follow = button("Follow this profile", () => {
      if (!sel.value) return setStatus(chooser.status, "Choose a profile.", true);
      act(() => post("api/nca/profiles/select", { name: sel.value }), "follow");
    }, "btn btn-primary btn-sm");
    follow.dataset.key = "follow";
    const stop = button("Stop following", () => act(() => post("api/nca/profiles/clear"), "follow"), "btn btn-ghost btn-sm");
    stop.disabled = !data.current?.selected;
    const again = button("Check again", () => refresh(), "btn btn-ghost btn-sm");
    const editIt = button("Edit", () => {
      if (!sel.value) return setStatus(chooser.status, "Choose a profile to edit.", true);
      edit({ name: sel.value });
    }, "btn btn-sm");
    editIt.setAttribute("aria-label", "Edit the chosen profile");
    const row = el("div", null, "nca-row");
    row.append(field("Profile in profiles/", sel), follow, stop, editIt, again);
    body.append(row);
    if (!data.study.length) body.append(el("p", "This study has no profiles yet. Copy an example below.", "muted"));

    for (const p of data.study.filter((x) => x.problems.length)) {
      const box = el("div", null, "nca-finding blocking");
      box.append(el("strong", `profiles/${p.name} can't be used: `), "fix these problems, then choose Check again.");
      const list = el("ul");
      for (const problem of p.problems) list.append(el("li", problem));
      const fix = button("Edit to fix", () => edit({ name: p.name }), "btn btn-sm");
      fix.setAttribute("aria-label", `Edit profiles/${p.name} to fix it`);
      box.append(list, fix);
      body.append(box);
    }

    const examples = el("div", null, "nca-body");
    examples.append(el("h3", "Examples"));
    const have = new Set(data.study.map((p) => p.name));
    for (const ex of data.examples) {
      const item = el("div", null, "panel");
      item.append(el("strong", ex.title || ex.name));
      if (ex.description) item.append(el("p", ex.description));
      const copy = button(have.has(ex.name) ? `profiles/${ex.name} exists` : "Copy into this study", async () => {
        try {
          const out = await post("api/nca/profiles/copy", { name: ex.name });
          setStatus(chooser.status, `Copied to ${out.path}. Choose it and Edit to match your protocol, then follow it.`);
          await refresh();
        } catch (e) {
          setStatus(chooser.status, e.message, true);
        }
      }, "btn btn-sm");
      copy.disabled = have.has(ex.name);
      if (!have.has(ex.name)) copy.setAttribute("aria-label", `Copy the example ${ex.title || ex.name} into this study`);
      const fresh = button("New from example", () => edit({ example: ex.name }), "btn btn-ghost btn-sm");
      fresh.setAttribute("aria-label", `New profile from the example ${ex.title || ex.name}`);
      const actions = el("div", null, "nca-row");
      actions.append(copy, fresh);
      item.append(actions);
      examples.append(item);
    }
    body.append(examples);
  }

  function renderSteps() {
    const body = steps.body;
    body.replaceChildren();
    hints.length = 0;
    const current = data.current;
    if (current && current.error) {
      body.append(el("p", current.error.message, "nca-finding blocking"));
      return;
    }
    if (!current || !current.selected) {
      body.append(el("p", data.workspace_open ? "Choose a profile to follow." : "", "muted"));
      return;
    }
    if (current.unreadable) {
      body.append(el("p", `profiles/${current.selected} can't be read: ${current.message} ` +
        "Restore or fix it, or choose another profile. Its progress is kept.", "nca-finding blocking"));
      return;
    }
    if (current.problems.length) {
      const box = el("div", null, "nca-finding blocking");
      box.append(el("strong", "The profile file has problems. Fix them to go on:"));
      const list = el("ul");
      for (const p of current.problems) list.append(el("li", p));
      box.append(list);
      body.append(box);
      return;
    }
    const locked = current.changed;
    if (locked) body.append(changedPanel(current));
    const done = current.steps.filter((s) => s.complete).length;
    const summary = el("p", `${done} of ${current.steps.length} steps done${current.complete ? ". Every step is done." : "."}`);
    body.append(summary);
    const list = el("ol", null, "nca-steps");
    current.profile.steps.forEach((step, i) => list.append(stepItem(step, current.steps[i], current, locked)));
    body.append(list);
    updateHints();
  }

  function changedPanel(current) {
    const box = el("div", null, "nca-finding warning");
    box.append(el("strong", `profiles/${current.selected} changed since it was adopted. `),
      "Progress can't change until you adopt the new text.");
    if (current.added.length) box.append(el("p", `New steps: ${current.added.join(", ")}`));
    if (current.dropped.length) box.append(el("p", `Removed steps (their progress is dropped): ${current.dropped.join(", ")}`));
    const adopt = button("Adopt edited profile", () => act(
      () => post("api/nca/profiles/current/adopt", { sha256: current.sha256 }), "follow"), "btn btn-primary btn-sm");
    box.append(el("p", null), adopt);
    return box;
  }

  function stepItem(step, status, current, locked) {
    const li = el("li");
    const headRow = el("div", null, "nca-step-head");
    headRow.append(el("strong", step.title), status.complete ? stateBadge("completed") : todoBadge());
    li.append(headRow);
    if (step.text) li.append(el("p", step.text));
    const key = (suffix) => `${step.id}:${suffix}`;
    const put = (url, body, k) => act(() => send("PUT", url, body), k);
    const stepUrl = `api/nca/profiles/current/steps/${encodeURIComponent(step.id)}`;

    if (step.type === "checklist") {
      const set = el("fieldset", null, "profile-checklist");
      set.append(el("legend", "Items", "visually-hidden"));
      const ticked = new Set(status.items || []);
      for (const item of step.items) {
        const label = el("label");
        const box = el("input");
        box.type = "checkbox";
        box.checked = ticked.has(item.id);
        box.disabled = locked;
        box.dataset.key = key(item.id);
        box.addEventListener("change", () => {
          const next = step.items.map((x) => x.id).filter((id) => (id === item.id ? box.checked : ticked.has(id)));
          put(stepUrl, { items: next }, key(item.id));
        });
        label.append(box, item.text);
        set.append(label);
      }
      li.append(set);
    } else if (step.type === "run") {
      li.append(runSlot(step, status, current, locked, put, key));
    } else if (step.type === "publication") {
      li.append(publicationChoice(step, status, current, locked, put, key));
    } else {
      if (step.type === "action") li.append(actionRow(step));
      const label = el("label", null, "nca-row");
      const box = el("input");
      box.type = "checkbox";
      box.checked = status.complete;
      box.disabled = locked;
      box.dataset.key = key("done");
      box.addEventListener("change", () => put(stepUrl, { done: box.checked }, key("done")));
      label.append(box, step.type === "checkpoint" ? "I have reviewed this" : "Done");
      li.append(label);
    }
    return li;
  }

  function actionRow(step) {
    const row = el("div", null, "nca-row");
    const target = catalog.targets.find((t) => t.id === step.target);
    const go = button(`Go to ${target ? target.title : "the control"}`, async () => {
      const found = await goToTarget(step.target);
      if (!found) setStatus(steps.status, "That control isn't on the page right now. It may appear after an earlier step.", true);
    }, "btn btn-sm");
    row.append(go);
    if (step.hint_condition) {
      const hint = el("span", null, "nca-args");
      hints.push([hint, step.hint_condition]);
      row.append(hint);
    }
    return row;
  }

  // A condition is only a hint: it never ticks a step.
  function updateHints() {
    for (const [node, id] of hints) {
      const c = catalog.conditions.find((x) => x.id === id);
      const value = checkCondition(id);
      const word = value === true ? "yes" : value === false ? "not yet" : "not known on this page";
      node.textContent = `Hint: ${c ? c.title : id}: ${word}.`;
    }
  }
  on("guidance-changed", updateHints);

  function runSlot(step, status, current, locked, put, key) {
    const box = el("div", null, "nca-body");
    const used = new Map(Object.entries(current.progress.slots).map(([slot, v]) => [v.run_id, slot]));
    const options = candidates.runs.map((r) => {
      const madeFor = r.workflow_context && r.workflow_context.step_id === step.id &&
        r.workflow_context.profile_path === current.path ? " · made for this step" : "";
      const other = used.has(r.run_id) && used.get(r.run_id) !== step.slot ? ` · in slot ${used.get(r.run_id)}` : "";
      return [r.run_id, `${r.run_id} · "${r.analyte.value}"${r.analyte.label ? ` (${r.analyte.label})` : ""}${madeFor}${other}`];
    });
    const sel = choice(options, status.run_id || "", "Choose a completed run…");
    sel.disabled = locked;
    sel.dataset.key = key("slot");
    const use = button("Use this run", () => {
      if (!sel.value) return setStatus(steps.status, "Choose a completed run.", true);
      put(`api/nca/profiles/current/slots/${encodeURIComponent(step.slot)}`, { run_id: sel.value }, key("slot"));
    }, "btn btn-sm");
    use.disabled = locked;
    const clear = button("Empty the slot", () => put(`api/nca/profiles/current/slots/${encodeURIComponent(step.slot)}`, { run_id: null }, key("slot")), "btn btn-ghost btn-sm");
    clear.disabled = locked || !status.run_id;
    const row = el("div", null, "nca-row");
    row.append(field(`Run for slot ${step.slot}`, sel), use, clear);
    box.append(row);
    const notes = [];
    if (step.suggested_label) notes.push(`Suggested analyte label: ${step.suggested_label}. Type it yourself on Start analysis if you want it.`);
    notes.push(`On Start analysis, choose "${step.title}" as the study profile step to record it with the run.`);
    if (status.run_id && !status.complete) notes.push(`Run ${status.run_id} is no longer a completed run: choose another.`);
    if (!candidates.runs.length) notes.push("There are no completed runs yet.");
    for (const n of notes) box.append(el("p", n, "nca-args"));
    return box;
  }

  function publicationChoice(step, status, current, locked, put, key) {
    const box = el("div", null, "nca-body");
    const wanted = step.slots.map((s) => current.progress.slots[s]?.run_id).filter(Boolean);
    const filled = wanted.length === step.slots.length;
    const exact = (p) => filled && p.runs.length === wanted.length && wanted.every((r) => p.runs.includes(r));
    const sel = choice(candidates.publications.map((p) => [p.publication_id,
      `${p.publication_id} · ${p.runs.length} run(s), ${p.items} file(s)${exact(p) ? " · holds exactly the slots' runs" : ""}`]),
    status.publication_id || "", "Choose a publication…");
    sel.disabled = locked;
    sel.dataset.key = key("pub");
    const url = `api/nca/profiles/current/publications/${encodeURIComponent(step.id)}`;
    const use = button("Use this publication", () => {
      if (!sel.value) return setStatus(steps.status, "Choose a publication.", true);
      put(url, { publication_id: sel.value }, key("pub"));
    }, "btn btn-sm");
    use.disabled = locked;
    const clear = button("Clear", () => put(url, { publication_id: null }, key("pub")), "btn btn-ghost btn-sm");
    clear.disabled = locked || !status.publication_id;
    const row = el("div", null, "nca-row");
    row.append(field("Publication", sel), use, clear);
    box.append(row,
      el("p", `It must hold exactly the runs in slots ${step.slots.join(", ")}${filled ? "" : " (fill those slots first)"}.`, "nca-args"));
    return box;
  }

  on("workspace-changed", refresh);
  on("view-changed", (route) => { if (route.name === "profile") refresh(); });
  refresh();
}

function todoBadge() {
  const badge = el("span", null, "pill pill-neutral state");
  const mark = el("span", "○");
  mark.setAttribute("aria-hidden", "true");
  badge.append(mark, " To do");
  return badge;
}
