"""Single-instance lock and state record (docs/spec.md, "Launch and stop contract").

A second icon click reopens the healthy running launcher instead of starting another.

- The **lock** is an OS file lock on ``<state>/instance.lock``, held for the life of the
  launcher. The OS drops it when the process dies, so a crash never leaves it held.
- The **state record** ``<state>/instance.json`` (schema ``instance-v1``) says where the
  running launcher listens: pid, pid start time, port, instance id, start time and
  version. It holds no session secret: a launch that reopens the browser mints a one-time
  ``/open`` link instead (``core.bootstrap``). ``atomic_write_json`` writes it
  through ``mkstemp``, so on POSIX it is readable by the user only (0600).

A launch that finds the lock held probes ``/health`` on the recorded port and checks the
instance id before opening the browser there. A record whose pid is alive but does not
answer is never acted on: nothing is ever killed because a record names it.
"""

from __future__ import annotations

import contextlib
import json
import logging
import sys
import time
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, Any

import psutil
from jsonschema import ValidationError

from opncafyr import __version__
from opncafyr.core.manifest import atomic_write_json, validate

log = logging.getLogger(__name__)

LOCK_FILE = "instance.lock"
RECORD_FILE = "instance.json"
RECORD_SCHEMA = "instance-v1"
LOOPBACK = "127.0.0.1"
# How long a launch waits for a lock holder that is still starting (or hung) to answer.
CLAIM_WAIT = 20.0


def instance_url(port: int) -> str:
    return f"http://{LOOPBACK}:{port}/"


class InstanceLock:
    """An exclusive, non-blocking OS lock on ``<state>/instance.lock``."""

    def __init__(self, state_dir: Path) -> None:
        self.path = Path(state_dir) / LOCK_FILE
        self._fh: IO[bytes] | None = None

    @property
    def held(self) -> bool:
        return self._fh is not None

    def try_acquire(self) -> bool:
        if self._fh is not None:
            return True
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fh = self.path.open("a+b")
        try:
            _lock(fh)
        except OSError:
            fh.close()
            return False
        self._fh = fh
        return True

    def release(self) -> None:
        fh, self._fh = self._fh, None
        if fh is None:
            return
        with contextlib.suppress(OSError):
            _unlock(fh)
        fh.close()


if sys.platform == "win32":
    import msvcrt

    def _lock(fh: IO[bytes]) -> None:
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)

    def _unlock(fh: IO[bytes]) -> None:
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def _lock(fh: IO[bytes]) -> None:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock(fh: IO[bytes]) -> None:
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


# --- state record ---------------------------------------------------------------------


def read_record(state_dir: Path) -> dict[str, Any] | None:
    """The recorded instance, or None if there is no readable, valid record."""
    path = Path(state_dir) / RECORD_FILE
    try:
        doc = json.loads(path.read_text("utf-8"))
        validate(doc, RECORD_SCHEMA)
    except FileNotFoundError:
        return None
    except (OSError, ValueError, ValidationError):
        log.warning("Ignoring an unreadable instance state record")
        return None
    return doc


def write_record(state_dir: Path, *, instance_id: str, port: int) -> dict[str, Any]:
    me = psutil.Process()
    doc = {
        "schema_version": "1.0",
        "instance_id": instance_id,
        "pid": me.pid,
        "pid_create_time": me.create_time(),
        "port": port,
        "started_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "version": __version__,
    }
    atomic_write_json(Path(state_dir) / RECORD_FILE, doc, RECORD_SCHEMA)
    return doc


def clear_record(state_dir: Path, instance_id: str) -> bool:
    """Remove the record only if it is ours. Returns True if it was removed."""
    record = read_record(state_dir)
    if record is None or record["instance_id"] != instance_id:
        return False
    (Path(state_dir) / RECORD_FILE).unlink(missing_ok=True)
    return True


def pid_alive(pid: int, create_time: float) -> bool:
    """True if ``pid`` is running and is the same process that was recorded."""
    try:
        return abs(psutil.Process(pid).create_time() - create_time) <= 0.5
    except psutil.Error:
        return False


def probe(port: int, instance_id: str, timeout: float = 1.0) -> bool:
    """True if the launcher on ``port`` answers ``/health`` with this instance id."""
    try:
        with urllib.request.urlopen(f"{instance_url(port)}health", timeout=timeout) as resp:  # noqa: S310 - fixed loopback URL
            if resp.status != 200:
                return False
            body = json.loads(resp.read())
    except (OSError, ValueError):
        return False
    return isinstance(body, dict) and body.get("instance_id") == instance_id


# --- claiming the instance ------------------------------------------------------------


@dataclass
class Claim:
    """The outcome of :func:`claim`: exactly one of the three fields is set."""

    lock: InstanceLock | None = None  # start a new instance; the lock is held
    url: str | None = None  # a healthy instance is already running here
    error: str | None = None  # a lock holder that never answered; nothing was touched


def claim(
    state_dir: Path,
    *,
    wait: float = CLAIM_WAIT,
    poll: float = 0.25,
    probe_timeout: float = 1.0,
) -> Claim:
    """Decide whether this launch starts a new instance or reopens the running one."""
    state_dir = Path(state_dir)
    lock = InstanceLock(state_dir)
    deadline = time.monotonic() + wait
    waiting = False
    while True:
        if lock.try_acquire():
            _note_previous(read_record(state_dir))
            return Claim(lock=lock)
        record = read_record(state_dir)
        if record is not None and probe(record["port"], record["instance_id"], probe_timeout):
            return Claim(url=instance_url(record["port"]))
        if time.monotonic() >= deadline:
            message = _diagnostic(record, state_dir, wait)
            log.error("%s", message)
            return Claim(error=message)
        if not waiting:
            log.info("Another launcher holds the lock but is not answering yet; waiting.")
            waiting = True
        time.sleep(poll)


def _note_previous(record: dict[str, Any] | None) -> None:
    """Log what the old record was. We hold the lock, so it was not a live launcher."""
    if record is None:
        return
    pid = record["pid"]
    if pid_alive(pid, record["pid_create_time"]):
        log.warning(
            "The state record names pid %s, which is running but does not hold the "
            "launcher lock, so it is not an opncafyr launcher (the pid was probably "
            "reused). It was left alone; starting a new instance.",
            pid,
        )
    else:
        log.info("Recovered a stale state record: pid %s is no longer running.", pid)


def _diagnostic(record: dict[str, Any] | None, state_dir: Path, wait: float) -> str:
    lock_path = state_dir / LOCK_FILE
    if record is None:
        return (
            f"Another opncafyr launcher holds {lock_path} but has not written its state "
            f"record after {wait:.0f}s. It was not stopped. Close it, or end that process, "
            "then launch again."
        )
    pid = record["pid"]
    if pid_alive(pid, record["pid_create_time"]):
        return (
            f"The running opncafyr launcher (pid {pid}, port {record['port']}, started "
            f"{record['started_at']}) did not answer its health check within {wait:.0f}s. "
            "It was not stopped. Close it, or end that process, then launch again."
        )
    return (
        f"Another process holds {lock_path}, but the launcher recorded there (pid {pid}) "
        "is no longer running. Nothing was stopped. Close any other opncafyr window, or "
        "end that process, then launch again."
    )
