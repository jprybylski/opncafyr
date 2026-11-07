// Settings → Support (#38): preview the redacted support bundle, then save it locally.
// Every entry is shown as plain text (textContent) before anything is written.

import { getJSON, post } from "./api.js";
import { $, el, setStatus } from "./dom.js";

let previewId = null;

const status = (text, isError) => setStatus($("support-status"), text, isError);

function kb(bytes) {
  return bytes < 1024 ? `${bytes} bytes` : `${(bytes / 1024).toFixed(1)} KB`;
}

async function listSaved() {
  const list = $("support-saved");
  try {
    const { bundles } = await getJSON("api/support/bundles");
    list.replaceChildren(
      ...bundles.slice(0, 5).map((b) => {
        const item = el("li");
        const link = el("a", b.name);
        link.href = `api/support/bundles/${encodeURIComponent(b.name)}`;
        link.setAttribute("download", b.name);
        item.append(link, ` (${kb(b.bytes)})`);
        return item;
      }),
    );
  } catch (e) {
    list.replaceChildren();
  }
}

async function onCreate() {
  const button = $("support-create");
  button.disabled = true;
  status("Collecting…");
  try {
    const preview = await getJSON("api/support/preview");
    previewId = preview.preview_id;
    $("support-entries").replaceChildren(
      ...preview.entries.map((entry) => {
        const details = el("details");
        const text = el("pre", entry.text || "(empty)");
        // Scrollable, so it must be reachable by keyboard.
        text.tabIndex = 0;
        text.setAttribute("aria-label", entry.name);
        details.append(el("summary", `${entry.name} (${kb(entry.bytes)})`), text);
        return details;
      }),
    );
    status("");
    $("support-dialog").showModal();
  } catch (e) {
    status(e.message, true);
  } finally {
    button.disabled = false;
  }
}

async function onClose() {
  const dialog = $("support-dialog");
  const id = previewId;
  previewId = null;
  if (dialog.returnValue !== "save" || !id) {
    status("Not saved.");
    return;
  }
  try {
    const saved = await post("api/support/bundle", { preview_id: id });
    status(`Saved ${saved.name} in the app's ${saved.folder} folder. It stays on this computer; download it below to send it.`);
    listSaved();
  } catch (e) {
    status(e.message, true);
  }
}

export function initSupport() {
  $("support-create").addEventListener("click", onCreate);
  $("support-dialog").addEventListener("close", onClose);
  listSaved();
}
