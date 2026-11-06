"""The run log, and the redacted support bundle written when an install fails.

A bundle is ``<app root>/logs/support/install-failure-<time>.zip`` with:
- ``system.json``: OS, architecture, free disk, opncafyr version, the failed step;
- ``steps.json``: each step's status and time;
- every log of the run, redacted.

Redaction (ADR 0007, ``core.redaction``): the home folder becomes ``~``, the user name
``<user>``, tokens (``ghp_…``, ``github_pat_…``, ``Authorization:`` headers, ``token=``)
are masked. The environment is never written. Installer logs hold no study data.
"""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import sys
import time
import zipfile
from pathlib import Path
from typing import Any

from opncafyr.core.redaction import Redactor

_TOKENS = re.compile(
    r"(?i)(gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}"
    r"|authorization:\s*\S+(?:\s+\S+)?|bearer\s+[A-Za-z0-9._~+/=-]{12,})"
)


class RunLog:
    """``<logs>/install/<time>-<mode>/``: installer.log plus one log per step."""

    def __init__(self, logs: Path, mode: str) -> None:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        self.dir = logs / "install" / f"{stamp}-{mode}"
        n = 1
        while self.dir.exists():
            n += 1
            self.dir = logs / "install" / f"{stamp}-{mode}-{n}"
        self.dir.mkdir(parents=True)
        self.main = self.dir / "installer.log"
        self.steps: list[dict[str, Any]] = []

    def write(self, message: str) -> None:
        with self.main.open("a", encoding="utf-8") as fh:
            fh.write(f"{time.strftime('%H:%M:%S')} {message}\n")

    def step(self, name: str, status: str, seconds: float, detail: str = "") -> None:
        self.steps.append(
            {"step": name, "status": status, "seconds": round(seconds, 1), "detail": detail}
        )


def redact(text: str, *, home: str | None = None, user: str | None = None) -> str:
    home = home if home is not None else str(Path.home())
    user = user if user is not None else (os.environ.get("USER") or os.environ.get("USERNAME"))
    text = _TOKENS.sub("<token>", text)
    for variant in sorted({home, home.replace("\\", "/"), home.replace("/", "\\")}, key=len)[::-1]:
        if variant and len(variant) > 1:
            text = text.replace(variant, "~")
    if user and len(user) > 1:
        text = re.sub(rf"(?<![A-Za-z0-9]){re.escape(user)}(?![A-Za-z0-9])", "<user>", text)
    # Secrets in query strings (token=…, secret=…), as in the launcher's own logs.
    return Redactor().redact(text)


def write_bundle(
    run: RunLog, support_dir: Path, *, failed_step: str, error: str, version: str
) -> Path:
    support_dir.mkdir(parents=True, exist_ok=True)
    path = support_dir / f"install-failure-{time.strftime('%Y%m%d-%H%M%S')}.zip"
    usage = shutil.disk_usage(support_dir)
    system = {
        "opncafyr": version,
        "os": platform.platform(),
        "machine": platform.machine(),
        "python": sys.version.split()[0],
        "free_disk_mb": usage.free // (1 << 20),
        "failed_step": failed_step,
        "error": redact(error),
        "time": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    tmp = path.with_suffix(".zip.tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("system.json", json.dumps(system, indent=2) + "\n")
        zf.writestr("steps.json", json.dumps(run.steps, indent=2) + "\n")
        for log in sorted(run.dir.glob("*.log")):
            text = log.read_text("utf-8", errors="replace")
            zf.writestr(f"logs/{log.name}", redact(text))
    os.replace(tmp, path)
    return path
