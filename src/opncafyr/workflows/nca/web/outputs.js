// Published files (#30): every table and figure in OUTPUTS/ and the check of its metadata
// sidecar. reportifyr only logs a missing or broken sidecar, so the launcher checks first.

import { button, el, getJSON, section, setStatus, table } from "./common.js";

const severityWord = { error: "Blocks", warning: "Warning" };

export function initOutputs(stack, state) {
  const sec = section(stack, "nca-outputs", "3. Published files",
    "Each file in OUTPUTS/ needs a metadata sidecar that matches it: the right path and " +
      "hash, and only abbreviations and footnote types that the standard footnotes define.", "nca.outputs.card");
  const run = button("Check published files", check, "btn btn-sm");
  sec.head.append(run);

  async function check() {
    run.disabled = true;
    setStatus(sec.status, "Checking OUTPUTS/…");
    try {
      const out = await getJSON("api/nca/outputs/check");
      render(out);
      const n = out.report.findings.length;
      setStatus(sec.status, out.files.length === 0
        ? "OUTPUTS/ holds no files yet."
        : n === 0
          ? `${out.files.length} file(s) checked; every sidecar matches.`
          : `${out.files.length} file(s) checked; ${n} finding(s).`, out.blocking);
    } catch (e) {
      setStatus(sec.status, e.message, true);
    } finally {
      run.disabled = false;
    }
  }

  function render(out) {
    sec.body.replaceChildren();
    const findings = out.report.findings;
    if (!findings.length) return;
    sec.body.append(table(["Level", "Check", "File", "Finding"], findings.map((f) => [
      severityWord[f.severity] || f.severity, f.check, el("span", f.path || "", "path"), f.message,
    ]), "Sidecar findings"));
  }

  state.renderOutputs = render;
  state.checkOutputs = check;
  state.onChange((what) => {
    if (what === "workspace" || what === "outputs") sec.body.replaceChildren();
  });
}
