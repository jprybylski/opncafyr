"""One writer at a time for workspace changes made outside the job system (#56).

A tool whose web app changes workspace files directly (its descriptor sets
``web_app.guard_mutations``) takes this lease for the length of each state-changing
request the proxy forwards to it. While the lease is held, jobs don't start and project
files aren't saved; while a job runs or another tool has unsaved or busy work, the proxy
refuses the request instead of forwarding it. The lease is tool-agnostic: it knows a
holder's name, never what the holder does.
"""

from __future__ import annotations

import threading


class WorkspaceLease:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._holder: str | None = None

    @property
    def holder(self) -> str | None:
        return self._holder

    def acquire(self, holder: str) -> bool:
        with self._lock:
            if self._holder is not None:
                return False
            self._holder = holder
            return True

    def release(self, holder: str) -> None:
        with self._lock:
            if self._holder == holder:
                self._holder = None
