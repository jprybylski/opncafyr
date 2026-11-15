"""Keep credentials and URL secrets out of anything datum's output reaches (#83).

datum substitutes ``${NAME}`` into URLs and headers, and its messages can echo a URL with
the value in it. Before a message is stored, shown or logged, ``Redactor.text`` replaces
every value the user entered for the job, every URL query string and every
``user:pass@``. ``scrub_file`` does the same to a job's ``stdout.log``/``stderr.log``.
"""

from __future__ import annotations

import base64
import re
from collections.abc import Iterable
from pathlib import Path
from urllib.parse import quote

MASK = "‹hidden›"
_URL_QUERY = re.compile(r"(?i)\b((?:https?|git|ssh)://[^\s\"'<>?#]*)\?[^\s\"'<>#]*")
_USERINFO = re.compile(r"(?i)\b((?:https?|git|ssh)://)[^/\s\"'<>@]*@")
_MIN_SECRET = 4


def _forms(value: str) -> set[str]:
    """A value as it may appear in output: raw, URL-encoded and in Basic auth."""
    forms = {value, quote(value, safe=""), quote(value)}
    forms.add(base64.b64encode(value.encode("utf-8")).decode("ascii"))
    return {f for f in forms if len(f) >= _MIN_SECRET}


class Redactor:
    """Masks the given secret values plus URL query strings and credentials."""

    def __init__(self, secrets: Iterable[str] = ()) -> None:
        values: set[str] = set()
        for s in secrets:
            if s:
                values |= _forms(s)
        # Longest first, so a value that contains another is masked whole.
        self._values = sorted(values, key=len, reverse=True)

    def text(self, value: str) -> str:
        out = value
        for secret in self._values:
            out = out.replace(secret, MASK)
        out = _USERINFO.sub(lambda m: f"{m.group(1)}{MASK}@", out)
        return _URL_QUERY.sub(lambda m: f"{m.group(1)}?{MASK}", out)

    def bytes(self, data: bytes) -> bytes:
        return self.text(data.decode("utf-8", "replace")).encode("utf-8")

    def scrub_file(self, path: Path) -> None:
        """Rewrite ``path`` with secrets masked; a missing file is fine."""
        try:
            data = path.read_bytes()
        except OSError:
            return
        clean = self.bytes(data)
        if clean != data:
            path.write_bytes(clean)

    def clear(self) -> None:
        self._values = []


def source_display(source: dict[str, object]) -> str:
    """A source for the status table: host and path only, never a query or credentials."""
    kind = source.get("type")
    if kind in ("http", "git"):
        url = str(source.get("url", ""))
        m = re.match(r"(?i)^[a-z][a-z0-9+.-]*://(?:[^/@]*@)?([^/?#]*)([^?#]*)", url)
        if not m:
            return "(invalid URL)"
        shown = f"{m.group(1)}{m.group(2)}"
        if kind == "git":
            ref = str(source.get("ref", ""))
            path = str(source.get("path", ""))
            shown = f"{shown} @ {ref}: {path}"
        return re.sub(r"\$\{[^}]*\}", "${…}", shown)
    if kind == "file":
        return str(source.get("path", ""))
    return f"({kind} source)"
