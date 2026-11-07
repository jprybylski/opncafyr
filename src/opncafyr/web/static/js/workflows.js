// Views added by workflow packs (core/registry.py WorkflowView). Core lists them from
// /api/workflows and knows nothing about what they do: each gets a sidenav link, an
// empty <div class="view">, and its module's init(container) is called once.

import { getJSON } from "./api.js";
import { el, icon } from "./dom.js";
import { registerView } from "./router.js";

export async function initWorkflows() {
  let data;
  try {
    data = await getJSON("api/workflows");
  } catch (e) {
    return; // the core views still work
  }
  const list = document.querySelector(".sidenav ul");
  const settings = list.querySelector('[data-view="settings"]')?.closest("li") || null;
  const main = document.getElementById("main");
  for (const pack of data.workflows || []) {
    for (const view of pack.views || []) {
      const link = el("a", null, "nav-link");
      link.href = `#/${view.id}`;
      link.dataset.view = view.id;
      link.title = view.title;
      link.append(icon(view.icon), el("span", view.title, "nav-label"));
      const item = el("li");
      item.append(link);
      list.insertBefore(item, settings);

      const container = el("div", null, "view");
      container.id = `view-${view.id}`;
      container.dataset.title = view.title;
      container.hidden = true;
      main.append(container);
      try {
        const module = await import(`../../${view.module}`);
        module.init(container);
      } catch (e) {
        container.append(el("p", `This page could not be loaded: ${e.message}`, "status error"));
      }
      registerView(view.id);
    }
  }
}
