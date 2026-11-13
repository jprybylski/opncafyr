"""Keep subject-level values and secrets out of logs (ADR 0007 section 4, #11).

Logs may carry counts, column names, check ids and row numbers, never cell values. The
first rule is not to log values at all. This module is the backstop:

- ``Redactor`` holds the values that must never be written: subject IDs and other
  identifier values. A workflow registers them when it reads a dataset
  (``register_values`` or ``register_from_csv``); ``redact`` replaces every whole-token
  occurrence with ``‹redacted›``. It also masks secrets in query strings
  (``token=…``), such as the one-time ``/open`` link in uvicorn's access log.
- ``RedactionFilter`` runs on every launcher log handler (``core.logs``). It replaces
  table-like arguments (data frames, lists of rows) with a row count, then redacts the
  formatted message and any traceback.
- Child-app output copied into ``logs/children/<tool>.log`` goes through ``redact`` line
  by line before it is written (``core.supervisor``).

Job ``stdout.log``/``stderr.log`` stay in the study workspace next to the run, where
ADR 0007 allows coded subject IDs. The support bundle (#38, ``core.support``) never
includes them, only their sizes.
"""

from __future__ import annotations

import csv
import logging
import re
import threading
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

REDACTED = "‹redacted›"
_SECRET_PARAMS = re.compile(r"(?i)\b((?:token|secret|session|api_token)=)[^&\s\"']+")
# A user name or token in a URL (https://user:token@host/…), as a Git source could carry (#70).
_URL_USERINFO = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[^/@\s\"']+@")


class Redactor:
    """A thread-safe set of values that must never reach a log."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._values: set[str] = set()
        self._pattern: re.Pattern[str] | None = None

    def __len__(self) -> int:
        return len(self._values)

    def register_values(self, values: Iterable[object]) -> int:
        """Add values (stripped, non-empty). Returns how many were new."""
        fresh = {str(v).strip() for v in values if v is not None} - {""}
        with self._lock:
            fresh -= self._values
            if fresh:
                self._values |= fresh
                self._pattern = _compile(self._values)
        return len(fresh)

    def register_from_csv(self, path: Path, columns: Iterable[str]) -> int:
        """Register every value of ``columns`` in a CSV file."""
        wanted = list(columns)
        values: list[str] = []
        with Path(path).open(newline="", encoding="utf-8-sig") as fh:
            for row in csv.DictReader(fh):
                values.extend(row[c] for c in wanted if row.get(c))
        return self.register_values(values)

    def clear(self) -> None:
        with self._lock:
            self._values.clear()
            self._pattern = None

    def redact(self, text: str) -> str:
        text = _SECRET_PARAMS.sub(lambda m: m.group(1) + REDACTED, text)
        text = _URL_USERINFO.sub(lambda m: m.group(1) + REDACTED + "@", text)
        pattern = self._pattern
        if pattern is not None:
            text = pattern.sub(REDACTED, text)
        return text

    def redact_bytes(self, raw: bytes) -> bytes:
        return self.redact(raw.decode("utf-8", "replace")).encode("utf-8")


def _compile(values: set[str]) -> re.Pattern[str]:
    # Longest first, so "TA-001" is matched whole before "001". A value counts only as a
    # whole token: not inside a longer word or identifier.
    alternatives = "|".join(re.escape(v) for v in sorted(values, key=len, reverse=True))
    return re.compile(rf"(?<![\w-])(?:{alternatives})(?![\w-])")


# The launcher's process-wide redactor. Workflows register values here.
REDACTOR = Redactor()


def _is_table(value: Any) -> bool:
    if hasattr(value, "shape") and (hasattr(value, "iloc") or hasattr(value, "columns")):
        return True  # pandas/polars-like
    if isinstance(value, (str, bytes, Mapping)) or not isinstance(value, Sequence):
        return False
    return bool(value) and all(
        isinstance(item, Mapping) or (isinstance(item, Sequence) and not isinstance(item, str))
        for item in value
    )


def _scrub(value: Any) -> Any:
    if _is_table(value):
        try:
            rows = len(value)
        except TypeError:
            rows = "?"
        return f"<{rows} rows redacted>"
    return value


class RedactionFilter(logging.Filter):
    """Redact every record before a handler writes it. Never drops a record."""

    def __init__(self, redactor: Redactor = REDACTOR) -> None:
        super().__init__()
        self.redactor = redactor
        self._formatter = logging.Formatter()

    def filter(self, record: logging.LogRecord) -> bool:
        if getattr(record, "_opncafyr_redacted", False):
            return True
        args = record.args
        if isinstance(args, Mapping):
            record.args = {k: _scrub(v) for k, v in args.items()}
        elif isinstance(args, tuple):
            record.args = tuple(_scrub(a) for a in args)
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 - a bad format string must not lose the record
            message = f"{record.msg!s} (unformattable arguments)"
        record.msg = self.redactor.redact(message)
        record.args = None
        if record.exc_info and not record.exc_text:
            record.exc_text = self._formatter.formatException(record.exc_info)
        if record.exc_text:
            record.exc_text = self.redactor.redact(record.exc_text)
        if record.stack_info:
            record.stack_info = self.redactor.redact(record.stack_info)
        record._opncafyr_redacted = True
        return True
