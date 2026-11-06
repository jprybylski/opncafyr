"""The launcher's own log: JSON lines in the app log folder (#9).

``configure_logging`` sends the root logger, and so uvicorn's loggers, to a rotating
``logs/launcher.log`` (one JSON object per line) and to a short stderr line. The
redaction filter (``core.redaction``, #11) runs on both before anything is written.
"""

from __future__ import annotations

import json
import logging
import sys
from collections.abc import Iterable
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

from opncafyr.core.redaction import RedactionFilter

LOG_FILE = "launcher.log"
MAX_BYTES = 5 * 1024 * 1024
BACKUPS = 3
_MARK = "_opncafyr_handler"


class JsonLineFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        doc = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_text or record.exc_info:
            doc["exception"] = record.exc_text or self.formatException(record.exc_info)
        return json.dumps(doc, ensure_ascii=False)


def configure_logging(
    log_dir: Path,
    *,
    level: int = logging.INFO,
    console: bool = True,
    filters: Iterable[logging.Filter] | None = None,
) -> Path:
    """Install the launcher's handlers on the root logger. Returns the log file path.

    ``filters`` defaults to the redaction filter; tests may pass their own.
    """
    filters = [RedactionFilter()] if filters is None else list(filters)
    reset_logging()
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / LOG_FILE
    handlers: list[logging.Handler] = []
    file_handler = RotatingFileHandler(
        path, maxBytes=MAX_BYTES, backupCount=BACKUPS, encoding="utf-8"
    )
    file_handler.setFormatter(JsonLineFormatter())
    handlers.append(file_handler)
    if console:
        stream = logging.StreamHandler(sys.stderr)
        stream.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
        handlers.append(stream)
    root = logging.getLogger()
    for handler in handlers:
        for f in filters:
            handler.addFilter(f)
        setattr(handler, _MARK, True)
        root.addHandler(handler)
    root.setLevel(level)
    return path


def reset_logging() -> None:
    """Remove and close the handlers ``configure_logging`` installed."""
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, _MARK, False):
            root.removeHandler(handler)
            handler.close()
