// Component reference page: a theme switch for reviewing both themes (not saved) and a
// live dialog example. No API calls.

const buttons = document.querySelectorAll("[data-set-theme]");
for (const button of buttons) {
  button.addEventListener("click", () => {
    const theme = button.dataset.setTheme;
    if (theme === "system") delete document.documentElement.dataset.theme;
    else document.documentElement.dataset.theme = theme;
    for (const other of buttons) other.setAttribute("aria-pressed", String(other === button));
  });
}

const dialog = document.getElementById("demo-dialog");
document.getElementById("demo-open").addEventListener("click", () => dialog.showModal());
