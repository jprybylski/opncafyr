"""Count the file and process work an operation does (#67), for tests and the bench.

Windows makes each file open, directory listing and process start far more expensive than
Linux (NTFS plus filter drivers such as Defender or a corporate endpoint agent), so work
that is cheap in a Linux container can make every click slow there. Durations measured on
a fast machine hide that; counts don't. ``count_io()`` records, for everything the process
does while it is active (any thread):

- ``open``: files opened (Python's ``open`` audit event);
- ``listdir``: ``os.listdir`` and ``os.scandir`` calls;
- ``stat``: ``os.stat``/``os.lstat`` calls (``Path.exists``, ``is_file``, ...);
- ``resolve``: ``os.path.realpath`` calls (``Path.resolve``; on Windows each opens a
  handle to the path);
- ``spawn``: processes started (``subprocess.Popen``), plus the program names in
  ``programs`` (the executable's stem only, never a path or argument).

Only counts are kept: no paths, file names or arguments.
"""

from __future__ import annotations

import os
import sys
import threading
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import PurePath

_lock = threading.Lock()
_active: list[IOCount] = []
_installed = False


@dataclass
class IOCount:
    open: int = 0
    listdir: int = 0
    stat: int = 0
    resolve: int = 0
    spawn: int = 0
    programs: Counter = field(default_factory=Counter)

    def as_dict(self) -> dict:
        return {
            "open": self.open,
            "listdir": self.listdir,
            "stat": self.stat,
            "resolve": self.resolve,
            "spawn": self.spawn,
            "programs": dict(sorted(self.programs.items())),
        }


def _program(executable, args) -> str:
    name = executable
    if name is None and args:
        name = args[0] if isinstance(args, list | tuple) else str(args).split()[0]
    try:
        return PurePath(os.fsdecode(name)).stem.lower() if name else "?"
    except (TypeError, ValueError):
        return "?"


def _hook(event: str, args: tuple) -> None:
    if not _active:
        return
    if event == "open":
        kind = "open"
    elif event in ("os.listdir", "os.scandir"):
        kind = "listdir"
    elif event == "subprocess.Popen":
        kind = "spawn"
    else:
        return
    with _lock:
        for c in _active:
            setattr(c, kind, getattr(c, kind) + 1)
            if kind == "spawn":
                c.programs[_program(args[0], args[1])] += 1


_inside = threading.local()


def _counting(fn, kind: str = "stat"):
    """Count one call per outermost call (``isfile`` calls ``stat`` on POSIX)."""

    def wrapper(*a, **kw):
        if not _active or getattr(_inside, "depth", 0):
            return fn(*a, **kw)
        with _lock:
            for c in _active:
                setattr(c, kind, getattr(c, kind) + 1)
        _inside.depth = 1
        try:
            return fn(*a, **kw)
        finally:
            _inside.depth = 0

    wrapper.__wrapped__ = fn
    return wrapper


# On Windows, os.path's exists/isfile/isdir/islink are native and don't call os.stat.
_PATH_CHECKS = ("exists", "lexists", "isfile", "isdir", "islink")


def _install() -> None:
    """Install the audit hook and the stat wrappers once (audit hooks can't be removed)."""
    global _installed
    if _installed:
        return
    sys.addaudithook(_hook)
    # pathlib and os.path look these up at call time, so the wrappers cover them too.
    os.stat = _counting(os.stat)
    os.lstat = _counting(os.lstat)
    for name in _PATH_CHECKS:
        setattr(os.path, name, _counting(getattr(os.path, name)))
    os.path.realpath = _counting(os.path.realpath, "resolve")
    _installed = True


@contextmanager
def count_io():
    """Count the file and process work done while the block runs (all threads)."""
    _install()
    c = IOCount()
    with _lock:
        _active.append(c)
    try:
        yield c
    finally:
        with _lock:
            _active.remove(c)
