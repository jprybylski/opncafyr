// Theme toggle (#50): System -> Light -> Dark. The server renders the saved choice as
// <html data-theme>, so the first paint is right; the choice is saved in the launcher's
// settings file because browser storage is lost when the port changes each launch.
// "system" matches no override rule in app.css, so the OS preference applies.

import { send } from "./api.js";
import { $, setStatus } from "./dom.js";

const THEMES = ["system", "light", "dark"];
const WORDS = { system: "System", light: "Light", dark: "Dark" };

function apply(theme) {
  document.documentElement.dataset.theme = theme;
  $("theme-label").textContent = `Theme: ${WORDS[theme]}`;
}

export function initTheme() {
  const saved = document.documentElement.dataset.theme;
  let theme = THEMES.includes(saved) ? saved : "system";
  apply(theme);
  $("theme-toggle").addEventListener("click", async () => {
    const before = theme;
    theme = THEMES[(THEMES.indexOf(theme) + 1) % THEMES.length];
    apply(theme);
    try {
      await send("PUT", "api/settings", { theme });
    } catch (e) {
      theme = before;
      apply(theme);
      setStatus($("app-status"), `Could not save the theme: ${e.message}`, true);
    }
  });
}
