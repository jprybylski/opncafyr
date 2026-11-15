// External data (#83, ADR 0012): declare, fetch and check the data a study depends on,
// through a managed datum. Nothing runs when the page opens except a local, offline
// audit; checks and fetches are jobs the user starts, and only a reviewed Apply changes
// files in source-data/. The page never polls on its own: it refreshes after each action
// and when a job finishes (the shell's job list already watches jobs while visible).

import { getJSON, post, send } from "../../js/api.js";
import { el, on, setStatus, formatTime } from "../../js/dom.js";
import { markTarget, notifyGuidance } from "../../js/guidance.js";
import { register as registerUnsaved, changed as unsavedChanged } from "../../js/unsaved.js";

const API = "api/datafetch";
const TERMINAL = new Set(["completed", "failed", "cancelled"]);

const EXPLAIN =
  "List the external data this study depends on, such as a file on a data portal, a file " +
  "on a network share or one file from a Git repository. The app fetches it into " +
  "source-data/ when you ask, shows what would change, and changes nothing until you " +
  "apply. Completed runs keep their own copy of their input and are never affected.";

const STATE_WORDS = {
  ok: ["✓", "Fetched", "ok"],
  pending: ["○", "Not fetched yet", "neutral"],
  deleted: ["■", "Deleted", "warn"],
  orphaned: ["!", "Not in settings", "warn"],
};

const CHECK_WORDS = {
  ok: ["✓", "No change", "ok"],
  updated: ["!", "Changed upstream", "warn"],
  fetched: ["!", "Changed upstream", "warn"],
  stale: ["!", "Changed upstream", "warn"],
  fail: ["!", "Changed upstream", "warn"],
  warn: ["!", "Warning", "warn"],
  error: ["✕", "Check failed", "danger"],
  deleted: ["■", "Deleted", "neutral"],
};

const ACTION_WORDS = {
  new: "New file",
  unchanged: "Unchanged",
  replace: "Will replace",
  remove: "Will remove",
};

function pill([symbol, word, tone]) {
  const p = el("span", null, `pill pill-${tone}`);
  const s = el("span", symbol);
  s.setAttribute("aria-hidden", "true");
  p.append(s, " ", word);
  return p;
}

function button(text, onClick, className = "btn btn-sm") {
  const b = el("button", text, className);
  b.type = "button";
  b.addEventListener("click", onClick);
  return b;
}

let uidCount = 0;
const uid = (prefix) => `${prefix}-${++uidCount}`;

function field(labelText, control, hint) {
  const wrap = el("div", null, "field");
  control.id = control.id || uid("df");
  const label = el("label", labelText);
  label.htmlFor = control.id;
  wrap.append(label, control);
  if (hint) {
    const h = el("p", hint, "df-hint");
    h.id = uid("dfh");
    control.setAttribute("aria-describedby", h.id);
    wrap.append(h);
  }
  return wrap;
}

function card(container, id, title, intro, target) {
  const sec = el("section", null, "card");
  sec.id = id;
  const head = el("div", null, "card-head");
  const h = el("h2", title);
  h.id = `${id}-heading`;
  head.append(h);
  sec.setAttribute("aria-labelledby", h.id);
  sec.append(head);
  if (intro) sec.append(el("p", intro, "card-note"));
  const body = el("div", null, "card-body df-body");
  const status = el("p", null, "status card-status");
  status.setAttribute("role", "status");
  status.setAttribute("aria-live", "polite");
  sec.append(body, status);
  if (target) markTarget(sec, target);
  container.append(sec);
  return { sec, head, body, status };
}

function bytes(n) {
  if (n === null || n === undefined) return "";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

const shortSha = (sha) => (sha ? sha.slice(0, 12) : "");
// A datum fingerprint ("sha256:…", "etag:…", "dirsha256:…") with its value shortened.
function shortFingerprint(fp) {
  const i = fp.indexOf(":");
  return i < 0 ? fp.slice(0, 16) : `${fp.slice(0, i + 1)}${fp.slice(i + 1, i + 13)}`;
}

function problemList(problems) {
  const ul = el("ul", null, "df-problems");
  for (const p of problems) {
    const where = p.dataset ? `${p.dataset}, ${p.field}` : p.field;
    ul.append(el("li", `${where}: ${p.message}`));
  }
  return ul;
}

// Wait for a job to end without polling: the shell's job list emits "job-finished".
function waitForJob(jobId) {
  return new Promise((resolve) => {
    let done = false;
    async function look() {
      if (done) return;
      try {
        const { job } = await getJSON(`api/jobs/${encodeURIComponent(jobId)}`);
        if (TERMINAL.has(job.state)) {
          done = true;
          resolve(job);
        }
      } catch (err) {
        // The next job-finished event looks again.
      }
    }
    on("job-finished", look);
    look();
  });
}

export function init(container) {
  const link = document.createElement("link");
  link.rel = "stylesheet";
  link.href = "static/workflows/datafetch/external-data.css";
  document.head.append(link);

  const head = el("div", null, "page-head");
  const h1 = el("h1", "External data");
  h1.id = "external-data-heading";
  h1.tabIndex = -1;
  const note = el("p", null, "muted");
  head.append(h1, el("p", EXPLAIN), note);
  const stack = el("div", null, "stack");
  container.append(head, stack);

  const statusCard = card(stack, "df-status", "Datasets", null, "datafetch.status");
  const credCard = card(
    stack,
    "df-credentials",
    "Credentials for this session",
    "Some datasets use ${NAME} values, such as an access token. Enter them here before a " +
      "check or fetch. They are kept in this page only, sent with that one job, and never " +
      "saved, logged or written to the study.",
  );
  const formCard = card(stack, "df-form", "Dataset", null);
  const reviewCard = card(
    stack,
    "df-review",
    "Review",
    null,
    "datafetch.review",
  );
  credCard.sec.hidden = true;
  formCard.sec.hidden = true;
  reviewCard.sec.hidden = true;

  let state = null;
  const credentials = new Map(); // name -> value, this page only
  let types = null;

  // --- refresh ------------------------------------------------------------------------

  let latest = 0;
  async function refresh() {
    const mine = ++latest;
    let next;
    try {
      next = await getJSON(`${API}/status`);
    } catch (err) {
      if (mine === latest) setStatus(statusCard.status, err.message || String(err), true);
      return;
    }
    if (mine !== latest) return;
    state = next;
    note.textContent = state.note || "";
    drawStatus();
    drawCredentials();
    notifyGuidance();
  }

  // Run an action, refresh, then report its error (a refresh never hides it).
  async function act(fn, where = statusCard.status) {
    let error = null;
    try {
      await fn();
    } catch (err) {
      error = err;
    }
    await refresh();
    if (error && !error.stopped) setStatus(where, errorText(error), true);
  }

  function errorText(err) {
    let text = err.message || String(err);
    const problems = err.detail?.problems || [];
    if (problems.length) {
      text += " " + problems.map((p) => `${p.dataset ? `${p.dataset}, ` : ""}${p.field}: ${p.message}`).join(" ");
    }
    if (err.detail?.datasets?.length) text += ` (${err.detail.datasets.join(", ")})`;
    if (err.detail?.missing?.length) text += ` Missing: ${err.detail.missing.join(", ")}.`;
    return text;
  }

  // --- status card --------------------------------------------------------------------

  function drawStatus() {
    const body = statusCard.body;
    body.replaceChildren();
    if (!state.workspace_open) {
      setStatus(statusCard.status, "Open a study folder first.");
      return;
    }
    if (!state.installed || !state.available) {
      setStatus(
        statusCard.status,
        `External data is unavailable${state.detail ? `: ${state.detail}` : "."} ` +
          "Everything else in the app works without it.",
      );
      return;
    }
    if (state.interrupted) drawInterrupted(body);
    if (!state.exists) {
      body.append(
        el(
          "p",
          `This study has no ${state.config_file} yet. Setting up creates an empty one at the ` +
            "study root. Nothing is fetched until you add a dataset and ask for it.",
        ),
      );
      const setup = button("Set up", () => act(() => post(`${API}/setup`, {})), "btn btn-primary");
      markTarget(setup, "datafetch.setup");
      body.append(el("div", null, "actions"));
      body.lastChild.append(setup);
      return;
    }
    if (state.config_error) {
      setStatus(statusCard.status, state.config_error, true);
      return;
    }
    if (state.problems?.length) {
      body.append(el("p", "These settings need fixing before a check or fetch:", "df-warn"));
      body.append(problemList(state.problems));
    }
    if (state.audit_error) body.append(el("p", `Status could not be read: ${state.audit_error}`, "df-warn"));

    const bar = el("div", null, "actions");
    const add = button("Add a dataset", () => openForm(null), "btn btn-sm");
    markTarget(add, "datafetch.add");
    const check = button("Check for updates", () => runCheck(), "btn btn-sm");
    markTarget(check, "datafetch.check");
    const fetchAll = button("Fetch all…", () => runFetch(null), "btn btn-sm btn-primary");
    markTarget(fetchAll, "datafetch.fetch");
    const blocked = Boolean(state.problems?.length) || !state.datasets?.length;
    check.disabled = blocked;
    fetchAll.disabled = blocked;
    bar.append(add, check, fetchAll);
    body.append(bar);
    if (state.last_check_at) {
      body.append(el("p", `Last checked ${formatTime(state.last_check_at)}.`, "muted"));
    }
    for (const r of state.reviews || []) {
      const p = el("p", `A fetch from ${formatTime(r.at)} is waiting for review. `, "df-warn");
      p.append(button("Open review", () => showReview(r.review_id)));
      body.append(p);
    }
    if (!state.datasets?.length) {
      body.append(el("p", "No datasets yet. Add one to get started.", "muted"));
      return;
    }
    body.append(datasetTable());
    for (const o of state.orphaned || []) {
      const p = el("p", `${o.id} is tracked in the lockfile but no longer in the settings. `, "muted");
      p.append(button("Forget lock entry…", () => forget(o.id)));
      body.append(p);
    }
  }

  function drawInterrupted(body) {
    const box = el("div", null, "notice df-interrupted");
    box.setAttribute("role", "alert");
    box.append(
      el(
        "p",
        "An earlier Apply was interrupted. Some files in source-data/ may already be " +
          "replaced while the lockfile still names the old state. Finish it, or roll back to " +
          "the files as they were before.",
      ),
    );
    const bar = el("div", null, "actions");
    bar.append(
      button("Finish applying", () =>
        act(() => post(`${API}/recover`, { action: "finish" })), "btn btn-sm btn-primary"),
      button("Roll back", () => act(() => post(`${API}/recover`, { action: "rollback" }))),
    );
    box.append(bar);
    body.append(box);
  }

  function datasetTable() {
    const wrap = el("div", null, "table-scroll");
    const t = el("table", null, "df-table");
    t.append(el("caption", "Datasets in the external data settings", "visually-hidden"));
    const thead = el("thead");
    const hr = el("tr");
    for (const h of ["Dataset", "Source", "Target", "Policy", "Status", "Last check", "Actions"]) {
      const th = el("th", h);
      th.scope = "col";
      hr.append(th);
    }
    thead.append(hr);
    const tbody = el("tbody");
    for (const d of state.datasets) tbody.append(datasetRow(d));
    t.append(thead, tbody);
    wrap.append(t);
    return wrap;
  }

  function datasetRow(d) {
    const tr = el("tr");
    const name = el("th");
    name.scope = "row";
    name.append(el("span", d.id, "df-id"));
    if (d.desc) name.append(el("span", d.desc, "df-desc muted"));
    tr.append(name);

    const src = el("td");
    d.types.forEach((type, i) => {
      src.append(el("span", `${type}: `, "muted"), el("span", d.sources[i], "path"), el("br"));
    });
    if (d.approval) {
      const ap = d.approval;
      if (ap.approved) src.append(pill(["✓", "Approved on this computer", "ok"]));
      else {
        src.append(pill(["!", "Approval needed", "warn"]), " ");
        src.append(button("Review…", () => approve(d)));
      }
    }
    tr.append(src);

    tr.append(el("td", d.target, "path df-target"));
    tr.append(el("td", d.policy));

    const st = el("td");
    const audit = d.audit;
    if (audit) st.append(pill(STATE_WORDS[audit.status] || ["•", audit.status, "neutral"]));
    if (d.fetched_at) {
      st.append(el("br"), el("span", `Applied ${formatTime(d.fetched_at)}`, "muted"));
      if (d.matches_fetched === false) {
        st.append(el("br"), pill(["!", "Local file changed since it was applied", "warn"]));
      }
    }
    const prev = state.previous?.[d.id];
    if (prev?.files) {
      st.append(el("br"), el("span", `${prev.files} earlier copies, ${bytes(prev.bytes)} `, "muted"));
      st.append(button("Purge…", () => purge(d.id, prev)));
    }
    if (d.problems?.length) st.append(el("br"), pill(["✕", "Needs fixing", "danger"]));
    tr.append(st);

    const chk = el("td");
    if (d.last_check) {
      chk.append(pill(CHECK_WORDS[d.last_check.status] || ["•", d.last_check.status, "neutral"]));
      if (d.last_check.message) chk.append(el("br"), el("span", d.last_check.message, "muted"));
    }
    tr.append(chk);

    const acts = el("td");
    const bar = el("div", null, "actions df-row-actions");
    const label = (word) => `${word} ${d.id}`;
    const fetchBtn = button("Fetch…", () => runFetch([d.id]));
    fetchBtn.setAttribute("aria-label", label("Fetch"));
    fetchBtn.disabled = Boolean(state.problems?.length) || audit?.status === "deleted";
    bar.append(fetchBtn);
    if (d.editable) {
      const edit = button("Edit", () => openForm(d));
      edit.setAttribute("aria-label", label("Edit"));
      bar.append(edit);
    }
    if (audit?.status === "deleted") {
      const u = button("Undelete", () => undelete(d.id));
      u.setAttribute("aria-label", label("Undelete"));
      bar.append(u);
    } else if (d.files?.length) {
      const del = button("Delete files…", () => deleteFiles(d), "btn btn-sm btn-danger");
      del.setAttribute("aria-label", label("Delete files of"));
      bar.append(del);
    }
    const rm = button("Remove…", () => removeDataset(d), "btn btn-sm btn-ghost");
    rm.setAttribute("aria-label", label("Remove"));
    bar.append(rm);
    acts.append(bar);
    tr.append(acts);
    return tr;
  }

  // --- confirmations (in-page, never a browser dialog) ---------------------------------

  function confirmBox(where, lines, yesText, onYes, danger = false) {
    where.querySelector(".df-confirm")?.remove();
    const box = el("div", null, "notice df-confirm");
    box.setAttribute("role", "group");
    box.setAttribute("aria-label", yesText);
    for (const line of lines) {
      if (Array.isArray(line)) {
        const ul = el("ul");
        for (const item of line) ul.append(el("li", item, "path"));
        box.append(ul);
      } else box.append(el("p", line));
    }
    const bar = el("div", null, "actions");
    const yes = button(yesText, async () => {
      box.remove();
      await act(onYes);
    }, danger ? "btn btn-sm btn-danger" : "btn btn-sm btn-primary");
    bar.append(yes, button("Cancel", () => box.remove()));
    box.append(bar);
    where.prepend(box);
    yes.focus();
  }

  function approve(d) {
    confirmBox(
      statusCard.body,
      [
        `${d.id} copies a file or folder from outside the study:`,
        [d.approval.path],
        "Approve it only if you expect this path. A shared settings file must not copy " +
          "something private, such as a key file, into the study. The approval is kept on " +
          "this computer only, never in the study.",
      ],
      "Approve this path",
      () => post(`${API}/datasets/${encodeURIComponent(d.id)}/approve`, { path: d.approval.path }),
    );
  }

  function deleteFiles(d) {
    confirmBox(
      statusCard.body,
      [
        `Delete the local files of ${d.id}? These files are removed from the study:`,
        d.files,
        "A copy of each is kept under .opncafyr/datafetch/previous/, and datum marks the " +
          "dataset deleted, so checks skip it. Undelete and fetch to get it back. Completed " +
          "runs are not affected.",
      ],
      "Delete files",
      () => post(`${API}/datasets/${encodeURIComponent(d.id)}/delete`, {}),
      true,
    );
  }

  function undelete(id) {
    confirmBox(
      statusCard.body,
      [`Track ${id} again? Nothing is fetched until you fetch it.`],
      "Undelete",
      () => post(`${API}/datasets/${encodeURIComponent(id)}/undelete`, {}),
    );
  }

  function forget(id) {
    confirmBox(
      statusCard.body,
      [
        `Forget the lockfile entry for ${id}? Its fingerprints are dropped from ` +
          ".data.lock.yaml. No files are removed.",
      ],
      "Forget lock entry",
      () => post(`${API}/datasets/${encodeURIComponent(id)}/forget`, {}),
    );
  }

  function purge(id, prev) {
    confirmBox(
      statusCard.body,
      [
        `Permanently delete the ${prev.files} earlier copies of ${id} (${bytes(prev.bytes)}) ` +
          "kept from replaced or deleted files? This can't be undone.",
      ],
      "Purge earlier copies",
      () => post(`${API}/datasets/${encodeURIComponent(id)}/purge`, {}),
      true,
    );
  }

  function removeDataset(d) {
    confirmBox(
      statusCard.body,
      [
        `Remove ${d.id} from ${state.config_file}? Its files in source-data/ stay where they ` +
          "are." + (state.has_comments ? " Comments in the settings file will be lost." : ""),
      ],
      "Remove dataset",
      async () => {
        const res = await post(`${API}/datasets/${encodeURIComponent(d.id)}/remove`, {
          expected_sha256: state.sha256,
        });
        if (res.offer_forget) {
          await refresh();
          forget(d.id);
        }
      },
    );
  }

  // --- credentials ----------------------------------------------------------------------

  function drawCredentials() {
    const names = state?.credentials || [];
    credCard.sec.hidden = !names.length || !state.available;
    credCard.body.replaceChildren();
    for (const name of names) {
      const input = el("input");
      input.type = "password";
      input.autocomplete = "off";
      input.spellcheck = false;
      input.value = credentials.get(name) || "";
      input.addEventListener("input", () => credentials.set(name, input.value));
      credCard.body.append(field(`\${${name}}`, input));
    }
    if (names.length) {
      credCard.body.append(
        button("Forget entered values", () => {
          credentials.clear();
          drawCredentials();
        }),
      );
    }
  }

  function credentialsFor() {
    const out = {};
    for (const name of state?.credentials || []) {
      const v = credentials.get(name);
      if (v) out[name] = v;
    }
    return out;
  }

  // --- check and fetch ----------------------------------------------------------------

  async function runCheck() {
    await act(async () => {
      setStatus(statusCard.status, "Checking for updates…");
      const { job_id } = await post(`${API}/check`, { credentials: credentialsFor() });
      const job = await waitForJob(job_id);
      if (job.state !== "completed") throw new Error(job.message);
    });
    if (!statusCard.status.classList.contains("error")) {
      setStatus(statusCard.status, "Check finished. Nothing in the study was changed.");
    }
  }

  async function runFetch(ids) {
    const what = ids ? ids.join(", ") : "every dataset";
    confirmBox(
      statusCard.body,
      [
        `Fetch ${what}? The data is downloaded or copied into a review folder first. ` +
          "Nothing in source-data/ changes until you review and apply it.",
      ],
      "Fetch",
      async () => {
        setStatus(statusCard.status, "Fetching…");
        const body = { credentials: credentialsFor() };
        if (ids) body.ids = ids;
        const { job_id, review_id } = await post(`${API}/fetch`, body);
        const job = await waitForJob(job_id);
        if (job.state !== "completed") throw new Error(job.message);
        setStatus(statusCard.status, "");
        await showReview(review_id);
      },
    );
  }

  // --- review ---------------------------------------------------------------------------

  async function showReview(reviewId) {
    const doc = await getJSON(`${API}/reviews/${encodeURIComponent(reviewId)}`);
    reviewCard.sec.hidden = false;
    const body = reviewCard.body;
    body.replaceChildren();
    setStatus(reviewCard.status, "");
    body.append(
      el(
        "p",
        "Applying replaces files in source-data/ with the fetched ones. Replaced files are " +
          "kept under .opncafyr/datafetch/previous/. Existing runs are not affected: each " +
          "keeps its own copy of its input. To analyse the new data, start a new run.",
      ),
      el("p", doc.note, "muted"),
    );
    let changes = 0;
    for (const ds of doc.datasets) {
      const h = el("h3", ds.id);
      body.append(h);
      const st = CHECK_WORDS[ds.status] || ["•", ds.status, "neutral"];
      const line = el("p");
      line.append(pill(ds.status === "fetched" ? ["✓", "Fetched", "ok"] : st));
      if (ds.message) line.append(" ", el("span", ds.message, "muted"));
      body.append(line);
      if (ds.remote_fingerprint) {
        const now = shortFingerprint(ds.remote_fingerprint);
        const text = !ds.lock_fingerprint
          ? `Source fingerprint: ${now} (first fetch)`
          : ds.lock_fingerprint === ds.remote_fingerprint
            ? `Source fingerprint: ${now} (unchanged)`
            : `Source fingerprint: ${shortFingerprint(ds.lock_fingerprint)} → ${now}`;
        body.append(el("p", text, "muted df-mono"));
      }
      for (const w of ds.warnings || []) body.append(el("p", `Warning: ${w}`, "df-warn"));
      if (!ds.files.length) {
        body.append(el("p", "No files would change.", "muted"));
        continue;
      }
      const wrap = el("div", null, "table-scroll");
      const t = el("table", null, "df-table");
      t.append(el("caption", `Files for ${ds.id}`, "visually-hidden"));
      const hr = el("tr");
      for (const hd of ["File", "Change", "Current", "New"]) {
        const th = el("th", hd);
        th.scope = "col";
        hr.append(th);
      }
      const thead = el("thead");
      thead.append(hr);
      const tbody = el("tbody");
      for (const f of ds.files) {
        if (f.action !== "unchanged") changes += 1;
        const tr = el("tr");
        tr.append(
          el("td", f.path, "path"),
          el("td", ACTION_WORDS[f.action] || f.action),
          el("td", f.current_sha256 ? `${bytes(f.current_size)} · ${shortSha(f.current_sha256)}` : "—", "df-mono"),
          el("td", f.new_sha256 ? `${bytes(f.new_size)} · ${shortSha(f.new_sha256)}` : "—", "df-mono"),
        );
        tbody.append(tr);
      }
      t.append(thead, tbody);
      wrap.append(t);
      body.append(wrap);
    }
    const bar = el("div", null, "actions");
    const applyBtn = button(
      changes ? `Apply ${changes} change${changes === 1 ? "" : "s"}` : "Apply (lockfile only)",
      () => act(async () => {
        await post(`${API}/reviews/${encodeURIComponent(reviewId)}/apply`, {});
        reviewCard.sec.hidden = true;
        setStatus(statusCard.status, "Applied. The next analysis of this data must be a new run.");
      }, reviewCard.status),
      "btn btn-primary",
    );
    markTarget(applyBtn, "datafetch.apply");
    bar.append(
      applyBtn,
      button("Discard", () => act(async () => {
        await send("DELETE", `${API}/reviews/${encodeURIComponent(reviewId)}`);
        reviewCard.sec.hidden = true;
        setStatus(statusCard.status, "Discarded. Nothing in the study changed.");
      }, reviewCard.status)),
    );
    body.append(bar);
    reviewCard.sec.scrollIntoView({ block: "nearest" });
    applyBtn.focus();
  }

  // --- add / edit form -------------------------------------------------------------------

  let form = null; // { dirty, original }
  const unregister = registerUnsaved("datafetch-form", "datum", () => Boolean(form?.dirty), save);
  void unregister;

  async function save() {
    if (!form) return true;
    try {
      await form.submit();
      return true;
    } catch (err) {
      return false;
    }
  }

  async function loadTypes() {
    if (types) return types;
    types = await getJSON(`${API}/types`);
    return types;
  }

  async function openForm(d) {
    try {
      await loadTypes();
    } catch (err) {
      setStatus(statusCard.status, err.message || String(err), true);
      return;
    }
    formCard.sec.hidden = false;
    formCard.head.querySelector("h2").textContent = d ? `Edit ${d.id}` : "Add a dataset";
    const body = formCard.body;
    body.replaceChildren();
    setStatus(formCard.status, "");
    if (state.has_comments) {
      body.append(
        el(
          "p",
          `${state.config_file} has comments. Saving rewrites the file, so its comments ` +
            "will be lost. Edit the file in a text editor instead if you want to keep them.",
          "df-warn",
        ),
      );
    }
    const src = d?.source || { type: "http" };
    const inputs = {
      id: Object.assign(el("input"), { value: d?.id || "", required: true }),
      desc: Object.assign(el("input"), { value: d?.desc || "" }),
      type: el("select"),
      url: Object.assign(el("input"), { value: src.url || "", type: "url" }),
      ref: Object.assign(el("input"), { value: src.ref || "" }),
      path: Object.assign(el("input"), { value: src.path || "" }),
      headers: Object.assign(el("textarea"), {
        value: Object.entries(src.headers || {}).map(([k, v]) => `${k}: ${v}`).join("\n"),
        rows: 3,
      }),
      body: Object.assign(el("textarea"), { value: src.body || "", rows: 2 }),
      target: Object.assign(el("input"), { value: d?.target || `${state.source_data}/` }),
      policy: el("select"),
    };
    for (const t of types.types) {
      const o = el("option", `${t.type}: ${t.description}`);
      o.value = t.type;
      inputs.type.append(o);
    }
    inputs.type.value = src.type;
    const def = el("option", "Default (fail)");
    def.value = "";
    inputs.policy.append(def);
    for (const p of types.policies) {
      const o = el("option", p);
      o.value = p;
      inputs.policy.append(o);
    }
    inputs.policy.value = d && d.policy !== "fail" ? d.policy : "";

    const fields = {
      url: field("URL", inputs.url, "https:// only. Put tokens in a ${NAME} credential, not the URL's user name."),
      ref: field("Branch or tag", inputs.ref),
      path: field("Path", inputs.path),
      headers: field("Headers (one per line, Name: value)", inputs.headers, "For example Authorization: Bearer ${TOKEN}."),
      body: field("Request body (optional; sends a POST)", inputs.body),
    };
    const pathHint = { file: "A file or folder. Outside the study, it needs your approval.", git: "The file's path inside the repository." };
    body.append(
      field("Id", inputs.id, "Letters, digits, - and _."),
      field("Description", inputs.desc),
      field("Source type", inputs.type),
      fields.url,
      fields.ref,
      fields.path,
      fields.headers,
      fields.body,
      field("Target", inputs.target, `Where the data goes, inside ${state.source_data}/.`),
      field("Policy", inputs.policy, "In the app every policy is report-only: a check never changes files. Fetch and apply to update."),
    );
    function showFields() {
      const type = inputs.type.value;
      fields.url.hidden = type === "file";
      fields.ref.hidden = type !== "git";
      fields.path.hidden = type === "http";
      fields.headers.hidden = type !== "http";
      fields.body.hidden = type !== "http";
      fields.path.querySelector(".df-hint")?.remove();
      if (pathHint[type]) fields.path.append(el("p", pathHint[type], "df-hint"));
    }
    showFields();

    const problems = el("div", null, "df-live");
    problems.setAttribute("aria-live", "polite");
    body.append(problems);

    function collect() {
      const type = inputs.type.value;
      const source = { type };
      if (type !== "file") source.url = inputs.url.value.trim();
      if (type !== "http") source.path = inputs.path.value.trim();
      if (type === "git") source.ref = inputs.ref.value.trim();
      if (type === "http") {
        const headers = {};
        for (const line of inputs.headers.value.split("\n")) {
          const i = line.indexOf(":");
          if (i > 0) headers[line.slice(0, i).trim()] = line.slice(i + 1).trim();
        }
        if (Object.keys(headers).length) source.headers = headers;
        if (inputs.body.value) source.body = inputs.body.value;
      }
      const out = {
        id: inputs.id.value.trim(),
        desc: inputs.desc.value.trim(),
        target: inputs.target.value.trim(),
        source,
      };
      if (inputs.policy.value) out.policy = inputs.policy.value;
      return out;
    }

    let timer = null;
    let validating = 0;
    async function validate() {
      const mine = ++validating;
      try {
        const res = await post(`${API}/validate`, { dataset: collect(), replace: d?.id || null });
        if (mine !== validating) return;
        problems.replaceChildren();
        if (res.problems.length) problems.append(problemList(res.problems));
      } catch (err) {
        if (mine !== validating) return;
        problems.replaceChildren(el("p", err.message || String(err), "df-warn"));
      }
    }

    form = {
      dirty: false,
      async submit() {
        const dataset = collect();
        const payload = { dataset, expected_sha256: state.sha256 };
        if (d) await send("PUT", `${API}/datasets/${encodeURIComponent(d.id)}`, payload);
        else await post(`${API}/datasets`, payload);
        form.dirty = false;
        unsavedChanged();
      },
    };
    body.addEventListener("input", () => {
      form.dirty = true;
      unsavedChanged();
      clearTimeout(timer);
      timer = setTimeout(validate, 300);
    });
    inputs.type.addEventListener("change", showFields);

    const bar = el("div", null, "actions");
    bar.append(
      button("Save", () => act(async () => {
        await form.submit();
        closeForm();
        setStatus(statusCard.status, `Saved ${state.config_file}.`);
      }, formCard.status), "btn btn-primary"),
      button("Cancel", () => closeForm()),
    );
    body.append(bar);
    inputs.id.focus();
  }

  function closeForm() {
    form = null;
    unsavedChanged();
    formCard.sec.hidden = true;
    formCard.body.replaceChildren();
  }

  refresh();
  on("workspace-changed", () => {
    credentials.clear();
    closeForm();
    reviewCard.sec.hidden = true;
    refresh();
  });
  on("view-changed", (route) => {
    if (route.name === "external-data") refresh();
  });
}
