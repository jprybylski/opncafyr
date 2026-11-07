"""A stand-in for ``Rscript`` running quartifyr's scripts (#32 unit tests).

Called as ``fake_rscript.py <fake root> --vanilla <script> [args]``. The wrapper written
by ``fake_runtime`` adds the root, because the job environment is allow-listed. What
``render.R`` does is set by ``mode.txt`` in that root (ok, error, garbage, badhash, hang,
missing-metadata). Every render also writes ``seen.json``: the files the project's
``OUTPUTS/`` held.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path


def arg(args: list[str], name: str) -> str:
    return args[args.index(name) + 1]


def setup() -> int:
    project = Path(os.environ["QUARTIFYR_SETUP_DIR"])
    (project / ".report_init.json").write_text("{}", "utf-8")
    for d in ("report/draft", "report/final", "report/shell", "OUTPUTS/tables", "OUTPUTS/figures"):
        (project / d).mkdir(parents=True, exist_ok=True)
    (project / "report" / "draft" / "readme.txt").write_text("x", "utf-8")
    (project / "report" / "standard_footnotes.yaml").write_text("abbreviations: {}\n", "utf-8")
    for ext in ("quartifyr", "A2-ai/quarto-plus"):
        (project / "_extensions" / ext).mkdir(parents=True)
        (project / "_extensions" / ext / "_extension.yml").write_text(ext, "utf-8")
    print("OPNCAFYR-PROGRESS: laid out")
    return 0


def check(args: list[str]) -> int:
    print(
        json.dumps(
            {
                "schema_version": 1,
                "findings": [
                    {
                        "check": "placeholder-last-in-section",
                        "severity": "warning",
                        "path": arg(args, "--shell"),
                        "message": "fine",
                    }
                ],
            }
        )
    )
    return 0


def render(args: list[str], here: Path) -> int:
    project = Path(arg(args, "--project"))
    status = arg(args, "--status")
    seen = sorted(
        p.relative_to(project).as_posix() for p in (project / "OUTPUTS").rglob("*") if p.is_file()
    )
    (here / "seen.json").write_text(
        json.dumps(
            {
                "files": seen,
                "cwd": os.getcwd(),
                "env": {
                    k: os.environ.get(k)
                    for k in ("HOME", "UV_OFFLINE", "QUARTIFYR_PYTHON_ENV", "PATH")
                },
            }
        ),
        "utf-8",
    )
    mode = (here / "mode.txt").read_text("utf-8").strip() if (here / "mode.txt").exists() else "ok"
    if mode == "garbage":
        print("Error in library(quartifyr): there is no package")
        return 0
    if mode == "hang":
        kw = {"start_new_session": True} if os.name != "nt" else {}
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"], **kw)
        (here / "pids.txt").write_text(f"{os.getpid()} {child.pid}", "utf-8")
        time.sleep(600)
        return 0
    out = project / "report" / status / f"report-{status}.docx"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(b"PK fake docx " + status.encode())
    sha = hashlib.sha256(out.read_bytes()).hexdigest()
    if mode == "badhash":
        sha = "0" * 64
    result = {
        "schema_version": 1,
        "status": "ok",
        "shell": None,
        "draft": str(out),
        "final": None,
        "artifacts": [
            {"role": status, "path": str(out), "sha256": sha, "bytes": out.stat().st_size}
        ],
        "warnings": [],
        "errors": [],
        "unfilled_placeholders": [],
        "missing_metadata": [],
        "inputs": [],
        "versions": {
            "quartifyr": "0.3.3",
            "reportifyr": "0.4.0",
            "quarto": "1.11.9",
            "libreoffice": None,
        },
        "started_at": "2026-11-07T00:00:00Z",
        "finished_at": "2026-11-07T00:00:01Z",
    }
    if mode in ("error", "missing-metadata"):
        result.update(
            status="error",
            error_class="quartifyr_render_incomplete",
            errors=[f"Render incomplete under {project}/report.qmd"],
        )
        if mode == "missing-metadata":
            result["missing_metadata"] = ["drug-a_summary.csv"]
        print(json.dumps(result))
        return 1
    print(json.dumps(result))
    return 0


def main() -> int:
    here, args = Path(sys.argv[1]), sys.argv[2:]
    assert args[0] == "--vanilla", args
    script, rest = Path(args[1]), args[2:]
    if script.name == "quartifyr_setup.R":
        return setup()
    if script.name == "check.R":
        return check(rest)
    if script.name == "render.R":
        return render(rest, here)
    print(f"unknown script {script}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
