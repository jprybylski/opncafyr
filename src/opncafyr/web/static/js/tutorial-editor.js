// The Tutorials page's editor (#76): opens the guidance form (guidance-form.js) for a
// study tutorial or a new one from an example, in place of the list. tutorial.js only
// announces "tutorial-edit"; it never edits a file itself.

import { $, emit, on } from "./dom.js";
import { openGuidanceForm } from "./guidance-form.js";

export function initTutorialEditor() {
  on("tutorial-edit", async (open) => {
    const host = $("tutorials-editor");
    const list = $("tutorials-card");
    list.hidden = true;
    const back = () => {
      list.hidden = false;
      emit("tutorials-changed");
      $("tutorials-heading").focus();
    };
    try {
      await openGuidanceForm(host, {
        api: "api/tutorials",
        open,
        unsavedKey: "tutorial",
        onClose: back,
        onSaved: () => emit("tutorials-changed"),
      });
    } catch (e) {
      host.hidden = true;
      list.hidden = false;
      $("tutorials-status").textContent = `The editor could not open: ${e.message}`;
    }
  });
}
