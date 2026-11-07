"""Process-tree helpers for tests that start real child processes."""

from __future__ import annotations

import time

import psutil


def tree_pids(pid: int) -> list[psutil.Process]:
    try:
        root = psutil.Process(pid)
        return [root, *root.children(recursive=True)]
    except psutil.Error:
        return []


def wait_gone(procs: list[psutil.Process], timeout: float = 15.0) -> list[psutil.Process]:
    """The processes from ``procs`` still alive (not zombies) after ``timeout``."""
    deadline = time.monotonic() + timeout
    while True:
        alive = []
        for p in procs:
            try:
                if p.is_running() and p.status() != psutil.STATUS_ZOMBIE:
                    alive.append(p)
            except psutil.Error:
                pass
        if not alive or time.monotonic() > deadline:
            return alive
        time.sleep(0.2)
