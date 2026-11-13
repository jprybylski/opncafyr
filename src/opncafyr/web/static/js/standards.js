// Standards (#70, ADR 0011): import a team's versioned profiles, tutorials and templates.
//
// Two steps, always: "Check and preview" verifies the source and shows exactly what install
// would do (files copied, conflicts with local edits, templates referenced); "Install" applies
// that preview. A conflict needs an explicit choice (keep mine, or install beside it); nothing
// is chosen by default. A Git source is fetched as a job first; the token is sent once and
// the field is cleared. Every value from a bundle is shown as text (textContent).

import { getJSON, post, send } from "./api.js";
import { $, el, formatTime, on, setStatus } from "./dom.js";

let info = null; // GET api/standards
let preview = null; // the last preview with a preview_id
const choices = new Map(); // conflict dest -> "keep" | "copy"

const importStatus = (t, e) => setStatus($("standards-import-status"), t, e);
const reviewStatus = (t, e) => setStatus($("standards-review-status"), t, e);
const listStatus = (t, e) => setStatus($("standards-list-status"), t, e);
const radio = (name) => document.querySelector(`input[name="${name}"]:checked`)?.value;
const short = (sha) => (sha ? sha.slice(0, 12) : "");
const TYPE_WORDS = { "report-template": "reports", "deck-template": "decks" };

function sourceWords(source) {
  if (!source) return "";
  const parts = [source.display];
  if (source.ref && source.ref !== "HEAD") parts.push(`ref ${source.ref}`);
  if (source.commit) parts.push(`commit ${short(source.commit)}`);
  if (source.type) parts.push(`type ${source.type}`);
  return parts.join(", ");
}

function list(items, className = "std-list") {
  const ul = el("ul", null, className);
  for (const item of items) ul.append(item instanceof Node ? item : el("li", item));
  return ul;
}

function pill(text, tone) {
  return el("span", text, `pill pill-${tone}`);
}

// --- import ---------------------------------------------------------------------------------

function syncForm() {
  const what = radio("std-what");
  $("std-type-field").hidden = what === "bundle";
  $("std-pins").hidden = what === "bundle";
  const git = radio("std-from") === "git";
  $("std-git-fields").hidden = !git;
  $("std-path-fields").hidden = git;
}

function templateBody() {
  const what = radio("std-what");
  if (what === "bundle") return {};
  const type = $("std-type").value.trim();
  const out = { template: { tool: what, ...(type ? { type } : {}) } };
  const commit = $("std-expect-commit").value.trim();
  const sha256 = $("std-expect-sha").value.trim();
  if (commit || sha256) out.expect = { ...(commit ? { commit } : {}), ...(sha256 ? { sha256 } : {}) };
  return out;
}

async function waitJob(jobId) {
  for (;;) {
    const { job } = await getJSON(`api/jobs/${encodeURIComponent(jobId)}`);
    if (["completed", "failed", "cancelled"].includes(job.state)) return job;
    importStatus(job.message || "Fetching…");
    await new Promise((r) => setTimeout(r, 500));
  }
}

async function onInspect(event) {
  event.preventDefault();
  const button = $("std-inspect");
  button.disabled = true;
  hideReview();
  try {
    const extra = templateBody();
    let source;
    if (radio("std-from") === "git") {
      const token = $("std-token").value;
      $("std-token").value = ""; // sent once, never kept in the page
      importStatus("Fetching with the app's Git…");
      const fetched = await post("api/standards/fetch", {
        repo: $("std-repo").value.trim(),
        ref: $("std-ref").value.trim() || null,
        subdir: $("std-subdir").value.trim() || null,
        token: token || null,
        ...(extra.template ? { template: extra.template } : {}),
      });
      const job = await waitJob(fetched.job_id);
      if (job.state !== "completed") {
        importStatus(job.message || "The fetch did not finish.", true);
        return;
      }
      source = { kind: "fetched", id: fetched.fetch_id };
    } else {
      source = { kind: "path", path: $("std-path").value.trim() };
    }
    importStatus("Checking every file…");
    const found = await post("api/standards/inspect", { source, ...extra });
    importStatus("");
    showReview(found);
  } catch (e) {
    importStatus(e.message, true);
  } finally {
    button.disabled = false;
  }
}

// --- review ---------------------------------------------------------------------------------

function hideReview() {
  preview = null;
  choices.clear();
  $("standards-review").hidden = true;
  reviewStatus("");
}

function planRow(row) {
  const item = el("li");
  const words = {
    new: ["New", "ok"],
    unchanged: ["Unchanged", "neutral"],
    update: ["Update (you haven't edited it)", "info"],
    conflict: [row.reason === "edited" ? "You edited this file" : "A file with this name is already there", "warn"],
  }[row.status];
  const head = el("div");
  head.append(el("span", row.dest, "path"), " ", pill(words[0], words[1]));
  item.append(head);
  if (row.status !== "conflict") return item;
  const group = el("fieldset");
  group.append(el("legend", `What to do with ${row.dest}`, "visually-hidden"));
  for (const [value, text] of [
    ["keep", `Keep mine (don't install the bundle's ${row.dest})`],
    ["copy", `Install the bundle's version beside it, as ${row.copy_to}`],
  ]) {
    const label = el("label");
    const input = el("input");
    input.type = "radio";
    input.name = `std-choice-${row.dest}`;
    input.value = value;
    input.addEventListener("change", () => {
      choices.set(row.dest, value);
      syncInstall();
    });
    label.append(input, " ", text);
    group.append(label);
  }
  item.append(group);
  if (row.diff) {
    const details = el("details");
    const pre = el("pre", row.diff.join("\n"), "std-diff");
    pre.tabIndex = 0;
    pre.setAttribute("aria-label", `Differences in ${row.dest}`);
    details.append(el("summary", "Show the differences"), pre);
    item.append(details);
  }
  return item;
}

function componentBlock(c, selectable) {
  const box = el("div", null, "std-component");
  const title = el("h3");
  const check = el("input");
  check.type = "checkbox";
  check.checked = true;
  check.dataset.component = c.id;
  check.setAttribute("aria-label", `Install ${c.title}`);
  check.addEventListener("change", syncInstall);
  title.append(check, " ", c.title, " ", pill(c.type_title, "neutral"));
  box.append(title);
  if (c.description) box.append(el("p", c.description, "muted"));
  if (c.links?.length) box.append(el("p", `Comes with: ${c.links.join(", ")}`, "muted"));
  if (c.ref) {
    const hit = c.resolved;
    box.append(
      el(
        "p",
        hit
          ? `Uses an imported template: ${hit.snapshot} (${hit.component}).`
          : `References a template that isn't imported yet. ${c.hint} The rest installs now; this one can be chosen once it is imported.`,
        hit ? "muted" : "notice",
      ),
    );
  }
  if (c.files.length) {
    const details = el("details");
    details.append(
      el("summary", `${c.files.length} file(s), SHA-256 checked`),
      list(c.files.map((f) => el("li", `${f.path} · ${f.bytes} bytes · ${short(f.sha256)}`, "std-hash"))),
    );
    box.append(details);
  }
  if (c.plan?.length) box.append(list(c.plan.map(planRow), "std-plan"));
  if (selectable && c.selectable && (c.ref ? c.resolved : true)) {
    const label = el("label");
    const use = el("input");
    use.type = "checkbox";
    use.dataset.select = c.id;
    label.append(use, ` Use it for new ${TYPE_WORDS[c.type] || "projects"}`);
    box.append(label);
  }
  return box;
}

function showReview(found) {
  const body = $("standards-review-body");
  body.replaceChildren();
  choices.clear();
  const head = found.bundle
    ? `${found.bundle.title} (${found.bundle.id} ${found.bundle.version})`
    : found.title;
  body.append(el("h3", head));
  const facts = el("ul", null, "facts");
  const fact = (k, v) => {
    const li = el("li", `${k} `);
    li.append(el("strong", v));
    facts.append(li);
  };
  fact("From", sourceWords(found.source));
  if (found.inventory_sha256) fact("SHA-256", short(found.inventory_sha256));
  if (found.snapshot) fact("Kept as", found.snapshot);
  if (found.already_imported) fact("Already imported", "yes");
  body.append(facts);
  if (found.bundle?.description) body.append(el("p", found.bundle.description));
  for (const row of found.compatibility || []) {
    body.append(el("p", `Needs ${row.what} ${row.range}: this app has ${row.have ?? "none"} (${row.ok ? "fits" : "doesn't fit"}).`, row.ok ? "muted" : "std-problems"));
  }
  if (found.problems.length) {
    body.append(el("p", "Nothing can be installed until these are fixed in the source:", "std-problems"));
    body.append(list(found.problems));
  }
  if (found.notes.length) {
    const details = el("details");
    details.append(el("summary", `Notes (${found.notes.length})`), list(found.notes));
    body.append(details);
  }
  if (found.variants?.length) {
    const field = el("div", null, "field");
    const label = el("label", "Variant");
    label.htmlFor = "std-variant";
    const select = el("select");
    select.id = "std-variant";
    select.append(new Option("Choose components below", ""));
    for (const v of found.variants) select.append(new Option(v.title, v.id));
    select.addEventListener("change", () => {
      const v = found.variants.find((x) => x.id === select.value);
      for (const box of body.querySelectorAll("input[data-component]")) {
        box.checked = !v || v.components.includes(box.dataset.component);
      }
      syncInstall();
    });
    field.append(label, select);
    body.append(field);
  }
  for (const c of found.components) body.append(componentBlock(c, !found.problems.length));
  preview = found.preview_id ? found : null;
  $("std-install").hidden = !preview;
  $("standards-review").hidden = false;
  syncInstall();
  $("standards-review-heading").scrollIntoView({ block: "start" });
}

function syncInstall() {
  if (!preview) return;
  const picked = new Set([...document.querySelectorAll("#standards-review-body input[data-component]:checked")].map((b) => b.dataset.component));
  const open = preview.components
    .filter((c) => picked.has(c.id))
    .flatMap((c) => (c.plan || []).filter((r) => r.status === "conflict" && !choices.has(r.dest)));
  $("std-install").disabled = picked.size === 0 || open.length > 0;
  reviewStatus(open.length ? `Choose what to do with ${open.map((r) => r.dest).join(", ")}.` : "");
}

async function onInstall() {
  if (!preview) return;
  const body = $("standards-review-body");
  const components = [...body.querySelectorAll("input[data-component]:checked")].map((b) => b.dataset.component);
  const select = [...body.querySelectorAll("input[data-select]:checked")].map((b) => b.dataset.select);
  $("std-install").disabled = true;
  try {
    const done = await post("api/standards/install", {
      preview_id: preview.preview_id,
      components: preview.kind === "template" ? null : components,
      choices: Object.fromEntries(choices),
      select,
    });
    hideReview();
    const files = done.files.length ? ` ${done.files.map((f) => `${f.path} (${f.action})`).join(", ")}.` : "";
    importStatus(`Installed ${done.snapshot}.${files}`);
    refresh();
  } catch (e) {
    reviewStatus(e.message, true);
    $("std-install").disabled = false;
  }
}

// --- imported and in use --------------------------------------------------------------------

async function choose(type, snapshot, component) {
  try {
    await send("PUT", "api/standards/selected", { type, snapshot, component });
    refresh();
  } catch (e) {
    listStatus(e.message, true);
  }
}

function renderUsed() {
  const box = $("standards-used");
  box.replaceChildren();
  for (const type of info.types.filter((t) => t.selectable)) {
    const chosen = info.selected?.[type.id];
    const row = el("p", null, "std-used");
    row.append(el("strong", `${type.title}: `));
    if (!chosen) {
      row.append("the bundled template.");
    } else {
      row.append(`${chosen.component} from ${chosen.snapshot}`);
      if (chosen.via) row.append(el("span", ` (through ${chosen.via.snapshot})`, "muted"));
      if (!chosen.ok) row.append(" ", pill("Changed", "danger"), el("p", chosen.problem, "std-problems"));
      const back = el("button", "Use the bundled one", "btn btn-ghost btn-sm");
      back.type = "button";
      back.addEventListener("click", () => choose(type.id, null, null));
      row.append(" ", back);
    }
    box.append(row);
  }
}

function renderList() {
  const box = $("standards-list");
  box.replaceChildren();
  if (!info.snapshots.length) {
    box.append(el("p", "Nothing imported yet. The bundled templates are used.", "muted"));
    return;
  }
  for (const snap of info.snapshots) {
    const block = el("div", null, "std-component");
    const title = el("h3", snap.kind === "bundle" ? `${snap.title} ${snap.version}` : snap.title);
    block.append(title);
    const facts = el("ul", null, "facts");
    facts.append(el("li", `From ${sourceWords(snap.source)}`), el("li", `Imported ${formatTime(snap.imported_at)}`), el("li", `Kept as ${snap.key}`));
    block.append(facts);
    const rows = snap.components.map((c) => {
      const li = el("li");
      li.append(`${c.title} `, pill(info.types.find((t) => t.id === c.type)?.title || c.type, "neutral"));
      if (c.ref && !c.resolved) li.append(el("span", ` Not available yet: ${c.hint}`, "muted"));
      const usable = info.types.find((t) => t.id === c.type)?.selectable && (!c.ref || c.resolved);
      const current = info.selected?.[c.type];
      const inUse = current && ((current.snapshot === snap.key && current.component === c.id) || (current.via?.snapshot === snap.key && current.via?.component === c.id));
      if (inUse) li.append(" ", pill("In use", "ok"));
      else if (usable) {
        const b = el("button", `Use for new ${TYPE_WORDS[c.type] || "projects"}`, "btn btn-sm");
        b.type = "button";
        b.addEventListener("click", () => choose(c.type, snap.key, c.id));
        li.append(" ", b);
      }
      return li;
    });
    block.append(list(rows));
    box.append(block);
  }
}

// One choice per template type that also takes a tool's own layout (the tool comes from the
// app's types, so this page names no tool).
function renderWhat() {
  const box = $("std-what-templates");
  if (box.childElementCount) return;
  for (const type of info.types.filter((t) => t.native)) {
    const label = el("label");
    const input = el("input");
    input.type = "radio";
    input.name = "std-what";
    input.value = type.native;
    input.addEventListener("change", syncForm);
    label.append(input, ` A ${type.title.toLowerCase()} in ${type.native}'s own layout`);
    box.append(label);
  }
}

async function refresh() {
  try {
    info = await getJSON("api/standards");
  } catch (e) {
    listStatus(e.message, true);
    return;
  }
  renderWhat();
  listStatus("");
  $("standards-closed").hidden = info.workspace_open;
  $("standards-body").hidden = !info.workspace_open;
  $("std-from-git").disabled = !info.git_available;
  $("std-git-missing").hidden = info.git_available;
  if (!info.git_available && radio("std-from") === "git") {
    document.querySelector('input[name="std-from"][value="path"]').checked = true;
    syncForm();
  }
  if (!info.workspace_open) return;
  renderUsed();
  renderList();
}

export function initStandards() {
  for (const input of document.querySelectorAll('input[name="std-what"], input[name="std-from"]')) {
    input.addEventListener("change", syncForm);
  }
  syncForm();
  $("standards-form").addEventListener("submit", onInspect);
  $("std-install").addEventListener("click", onInstall);
  $("std-cancel").addEventListener("click", hideReview);
  $("standards-refresh").addEventListener("click", refresh);
  on("view-changed", (route) => {
    if (route.name === "standards") refresh();
  });
  on("workspace-changed", () => {
    hideReview();
    refresh();
  });
}
