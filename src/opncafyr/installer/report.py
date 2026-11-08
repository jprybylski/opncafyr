"""The installation verification report (#36), written after every install, update or repair.

``<app root>/state/reports/install-report-<time>.json`` (schema ``install-report-v1``) and a
self-contained ``.html`` next to it. They record:
- the opncafyr version and the installer payload digest;
- the bundle ID, if the install came from a bundle, and how files were trusted (the pins
  inside the payload; no detached signature until #39);
- OS, architecture and paths;
- the version and SHA-256 of every installed component;
- what happened to each best-effort optional component (Git, #56): installed, or why not;
- the smoke-test results: an opnca-r calculation against known values, a Quarto DOCX
  render with an R chunk, a deckifyr build.

A customer's own qualification process (e.g. IQ) may use these files as evidence. opncafyr
is for research use only and makes no validation claim, and the report says so.
"""

from __future__ import annotations

import html
import json
import platform
import time
from pathlib import Path
from typing import Any

from opncafyr.installer.layout import Layout
from opncafyr.installer.records import write_json

NOTICE = (
    "Research use only. This report records what was installed and that its parts started "
    "and agreed with the pinned versions. It is not a validation of opncafyr or of any "
    "calculation."
)
TRUST = (
    "Every installed file was checked against the SHA-256 pinned inside the installer "
    "payload (digest below) before it was used. No detached signature was checked: until "
    "code signing (#39), compare the payload digest with the value published for this "
    "release."
)


def build(
    layout: Layout, record: dict[str, Any], *, bundle: dict[str, Any] | None
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "notice": NOTICE,
        "opncafyr_version": record["version"],
        "install_id": record["install_id"],
        "mode": record["mode"],
        "source": record["source"],
        "payload_digest": record["payload_digest"],
        "bundle": (
            {"bundle_id": bundle.get("bundle_id", ""), "platform": bundle.get("platform", "")}
            if bundle
            else None
        ),
        "signature": {"checked": False, "trust": TRUST},
        "system": {
            "platform": record["platform"],
            "os": record["os"],
            "machine": platform.machine(),
        },
        "paths": record["paths"],
        "components": record["components"],
        "optional": record.get("optional", []),
        "smoke": record["smoke"],
        "result": "pass" if all(r["status"] == "ok" for r in record["smoke"]) else "fail",
    }


def write(layout: Layout, report: dict[str, Any]) -> tuple[Path, Path]:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    directory = layout.state / "reports"
    json_path = write_json(directory / f"install-report-{stamp}.json", report)
    html_path = directory / f"install-report-{stamp}.html"
    tmp = html_path.with_suffix(".html.tmp")
    tmp.write_text(render_html(report), "utf-8")
    tmp.replace(html_path)
    return json_path, html_path


def _rows(items: list[list[str]]) -> str:
    return "\n".join(
        "<tr>" + "".join(f"<td>{html.escape(str(c))}</td>" for c in row) + "</tr>" for row in items
    )


def render_html(report: dict[str, Any]) -> str:
    """A printable page: no scripts, no external resources."""
    e = html.escape
    smoke = _rows(
        [[s["name"], s["status"], f"{s['seconds']} s", s["detail"]] for s in report["smoke"]]
    )
    comps = []
    for c in report["components"]:
        files = c["files"]
        if len(files) <= 3:
            for f in files:
                comps.append([c["name"], c["version"], f["file"], f["sha256"]])
        else:
            comps.append([c["name"], c["version"], f"{len(files)} files", "see the JSON report"])
    optional = ""
    if report.get("optional"):
        rows = _rows([[o["name"], o["status"], o["detail"]] for o in report["optional"]])
        optional = (
            "<h2>Optional components</h2>\n"
            "<p>Installed by default when possible; without one, everything else still works."
            "</p>\n<table><tr><th>Component</th><th>Status</th><th>Detail</th></tr>"
            f"{rows}</table>\n"
        )
    bundle = report["bundle"]
    facts = [
        ["Result", report["result"]],
        ["opncafyr", report["opncafyr_version"]],
        ["Install", f"{report['install_id']} ({report['mode']}, source: {report['source']})"],
        ["Bundle ID", bundle["bundle_id"] if bundle else "not installed from a bundle"],
        ["Installer payload digest (SHA-256)", report["payload_digest"]],
        ["Signature", "not checked: " + report["signature"]["trust"]],
        ["Platform", f"{report['system']['platform']} ({report['system']['os']})"],
        ["App root", report["paths"]["app_root"]],
        ["Install folder", report["paths"]["version_dir"]],
        ["R folder", report["paths"]["r_dir"]],
        ["Generated", report["generated_at"]],
    ]
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>opncafyr installation report</title>
<style>
body {{ font: 14px/1.4 system-ui, sans-serif; margin: 2rem; color: #111; background: #fff; }}
table {{ border-collapse: collapse; margin: 1rem 0; width: 100%; }}
td, th {{ border: 1px solid #bbb; padding: 4px 8px; text-align: left; vertical-align: top; }}
td:last-child {{ word-break: break-all; }}
.notice {{ border-left: 4px solid #b45309; padding: 8px 12px; background: #fffbeb; }}
</style></head><body>
<h1>opncafyr installation report</h1>
<p class="notice">{e(report["notice"])}</p>
<h2>Summary</h2>
<table>{_rows(facts)}</table>
<h2>Smoke tests</h2>
<table><tr><th>Check</th><th>Status</th><th>Time</th><th>Detail</th></tr>{smoke}</table>
<h2>Components</h2>
<table><tr><th>Component</th><th>Version</th><th>File</th><th>SHA-256</th></tr>{_rows(comps)}</table>
{optional}</body></html>
"""


def dumps(report: dict[str, Any]) -> str:
    return json.dumps(report, indent=2, sort_keys=True)
