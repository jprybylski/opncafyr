"""When the launcher stops itself (docs/spec.md, "Launch and stop contract"; ADR 0003).

- **Pages.** The home page sends a heartbeat while it is visible. A page counts as open
  until ``heartbeat_window_s`` passes without one. Closing the tab sends a best-effort
  ``leave``, which is only a hint.
- **Idle shutdown.** The launcher is idle when no page is open, no job is live and no
  child reports unsaved edits or busy work. After ``idle_timeout_s`` of continuous idle
  (30 minutes by default, 5 minutes up to off), it stops.
- **Unsaved editors in the page (#52).** A heartbeat may list the tools whose editor in
  that page has unsaved text. They count like a child's unsaved edits, and are kept for
  ``unsaved_window_s`` (longer than the page window, as hidden tabs beat rarely) or until
  the page reports them saved or leaves.
- **Quit.** An explicit Quit is refused while anything is unsaved, until the user
  chooses. ``save`` lets children save themselves as they stop; it can't save text held
  in a page, so the page saves its own editors first. ``discard`` quits regardless.
  With live jobs the default keeps them running: new jobs are refused and the launcher
  stops once they finish. Choosing ``cancel`` stops at once.
- **Order.** Stop accepting jobs, stop the children, then close the listener. The CLI
  clears the state record after the server returns.

The class takes the job manager and two callables, so it knows nothing about any tool.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

from jsonschema import ValidationError

from opncafyr.core.adapter import ChildActivity
from opncafyr.core.manifest import atomic_write_json, validate

log = logging.getLogger(__name__)

DEFAULT_IDLE_MINUTES = 30
MIN_IDLE_MINUTES = 5
MAX_IDLE_MINUTES = 24 * 60
HEARTBEAT_WINDOW_S = 45.0
UNSAVED_WINDOW_S = 15 * 60.0
MAX_UNSAVED_TOOLS = 16
TOOL_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
TICK_S = 5.0
MAX_PAGES = 64
PAGE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

SETTINGS_FILE = "settings.json"
SETTINGS_SCHEMA = "settings-v1"

JobsChoice = Literal["keep", "cancel"]
UnsavedChoice = Literal["refuse", "save", "discard"]


class JobsView(Protocol):
    """The parts of ``core.jobmanager.JobManager`` the lifecycle uses."""

    def active_count(self) -> int: ...

    def stop_accepting(self) -> None: ...

    def resume_accepting(self) -> None: ...

    def shutdown(self, timeout: float = ...) -> None: ...


@dataclass(frozen=True)
class QuitResult:
    status: Literal["stopping", "stopping_after_jobs", "needs_choice"]
    jobs: int = 0
    reasons: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class LifecycleConfig:
    """Launch options. ``override_idle`` makes ``idle_timeout_s`` win over the setting."""

    tick_s: float = TICK_S
    heartbeat_window_s: float = HEARTBEAT_WINDOW_S
    override_idle: bool = False
    idle_timeout_s: float | None = None


class Lifecycle:
    def __init__(
        self,
        *,
        jobs: JobsView,
        activity: Callable[[], Mapping[str, ChildActivity]],
        stop_children: Callable[[], None],
        request_shutdown: Callable[[], None],
        idle_timeout_s: float | None,
        heartbeat_window_s: float = HEARTBEAT_WINDOW_S,
        unsaved_window_s: float = UNSAVED_WINDOW_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.jobs = jobs
        self._activity = activity
        self._stop_children = stop_children
        self._request_shutdown = request_shutdown
        self.idle_timeout_s = idle_timeout_s
        self.heartbeat_window_s = heartbeat_window_s
        self.unsaved_window_s = unsaved_window_s
        self._clock = clock
        self._lock = threading.Lock()
        self._pages: dict[str, float] = {}
        self._page_unsaved: dict[str, tuple[frozenset[str], float]] = {}
        self._idle_since: float | None = None
        self._quit_pending = False
        self._stopping = False
        self.stop_reason: str | None = None
        self.stopped = threading.Event()

    # --- pages -----------------------------------------------------------------------

    def heartbeat(self, page_id: str, unsaved: tuple[str, ...] | list[str] = ()) -> None:
        if not PAGE_ID_RE.match(page_id):
            raise ValueError("Invalid page id.")
        if len(unsaved) > MAX_UNSAVED_TOOLS or not all(TOOL_ID_RE.match(t) for t in unsaved):
            raise ValueError("Invalid unsaved list.")
        with self._lock:
            now = self._clock()
            self._expire(now)
            if page_id not in self._pages and len(self._pages) >= MAX_PAGES:
                oldest = min(self._pages, key=self._pages.__getitem__)
                del self._pages[oldest]
            self._pages[page_id] = now
            if unsaved:
                self._page_unsaved[page_id] = (frozenset(unsaved), now)
            else:
                self._page_unsaved.pop(page_id, None)
            self._idle_since = None

    def leave(self, page_id: str) -> None:
        with self._lock:
            self._pages.pop(page_id, None)
            self._page_unsaved.pop(page_id, None)

    def _expire(self, now: float) -> None:
        cutoff = now - self.heartbeat_window_s
        for page, seen in list(self._pages.items()):
            if seen < cutoff:
                del self._pages[page]
        cutoff = now - self.unsaved_window_s
        for page, (_, seen) in list(self._page_unsaved.items()):
            if seen < cutoff:
                del self._page_unsaved[page]

    def _pages_unsaved(self) -> list[str]:
        with self._lock:
            self._expire(self._clock())
            return sorted({t for tools, _ in self._page_unsaved.values() for t in tools})

    def page_unsaved(self, tool: str) -> bool:
        """True while some page reports unsaved editor text for ``tool``."""
        return tool in self._pages_unsaved()

    # --- the periodic check ----------------------------------------------------------

    def check(self) -> None:
        """Run on every monitor tick: stop when idle long enough or a quit is due."""
        if self._stopping:
            return
        jobs = self.jobs.active_count()
        unsaved, busy = self._children()
        with self._lock:
            if self._stopping:
                return
            now = self._clock()
            self._expire(now)
            if self._quit_pending:
                if jobs == 0 and not busy:
                    self._begin_stop("quit after jobs finished")
                return
            if self._pages or jobs or unsaved or busy:
                self._idle_since = None
                return
            if self._idle_since is None:
                self._idle_since = now
            timeout = self.idle_timeout_s
            if timeout is not None and now - self._idle_since >= timeout:
                self._begin_stop("idle")

    def _children(self) -> tuple[list[str], list[str]]:
        activity = self._activity()
        unsaved = sorted(
            {tool for tool, a in activity.items() if a.unsaved} | set(self._pages_unsaved())
        )
        busy = sorted(tool for tool, a in activity.items() if a.busy)
        return unsaved, busy

    # --- explicit quit ---------------------------------------------------------------

    def request_quit(
        self, jobs: JobsChoice = "keep", unsaved: UnsavedChoice = "refuse"
    ) -> QuitResult:
        unsaved_tools, busy = self._children()
        if unsaved_tools and unsaved == "refuse":
            return QuitResult("needs_choice", reasons=["unsaved_changes"], tools=unsaved_tools)
        in_pages = self._pages_unsaved()
        if in_pages and unsaved == "save":
            # Only the page holding the text can save it.
            return QuitResult("needs_choice", reasons=["unsaved_in_page"], tools=in_pages)
        live = self.jobs.active_count()
        with self._lock:
            if self._stopping:
                return QuitResult("stopping")
            if (live or busy) and jobs == "keep":
                self.jobs.stop_accepting()
                self._quit_pending = True
                log.info("Quit requested; stopping after %s live job(s) finish", live)
                return QuitResult("stopping_after_jobs", jobs=live, tools=busy)
            self._begin_stop("quit" if not live else "quit, jobs cancelled")
            return QuitResult("stopping", jobs=live)

    def abort_quit(self) -> bool:
        """Keep the launcher open after a keep-running quit. False if already stopping."""
        with self._lock:
            if self._stopping:
                return False
            if self._quit_pending:
                self._quit_pending = False
                self.jobs.resume_accepting()
            return True

    # --- status ----------------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        jobs = self.jobs.active_count()
        unsaved, busy = self._children()
        with self._lock:
            now = self._clock()
            self._expire(now)
            stop_in = None
            if self.idle_timeout_s is not None and self._idle_since is not None:
                stop_in = max(0.0, self.idle_timeout_s - (now - self._idle_since))
            timeout = self.idle_timeout_s
            return {
                "pages": len(self._pages),
                "jobs": jobs,
                "unsaved": unsaved,
                "busy": busy,
                "idle_timeout_minutes": None if timeout is None else round(timeout / 60),
                "idle_stop_in_s": stop_in,
                "quit_pending": self._quit_pending,
                "stopping": self._stopping,
            }

    # --- stopping --------------------------------------------------------------------

    def _begin_stop(self, reason: str) -> None:
        """Caller holds the lock. The stop runs on its own thread so callers return."""
        self._stopping = True
        self.stop_reason = reason
        log.info("Stopping the launcher (%s)", reason)
        threading.Thread(target=self._stop_sequence, name="lifecycle-stop", daemon=True).start()

    def _stop_sequence(self) -> None:
        try:
            self.jobs.shutdown()  # refuses new jobs; cancels any live one (user chose)
            self._stop_children()
        except Exception:
            log.exception("Error while stopping; closing the listener anyway")
        finally:
            self._request_shutdown()
            self.stopped.set()


# --- user settings ---------------------------------------------------------------------

THEMES = ("system", "light", "dark")
DEFAULT_THEME = "system"


class Settings:
    """User settings in the app state dir (``settings-v1``): idle timeout and theme.

    Each setter rewrites only its own field, so saving one setting keeps the others.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()

    def _read(self) -> dict[str, object]:
        try:
            doc = json.loads(self.path.read_text("utf-8"))
            validate(doc, SETTINGS_SCHEMA)
        except FileNotFoundError:
            return {}
        except (OSError, ValueError, ValidationError):
            log.warning("Ignoring an unreadable settings file; using the defaults")
            return {}
        return doc

    def _update(self, **fields: object) -> None:
        with self._lock:
            doc = {**self._read(), **fields, "schema_version": "1.0"}
            atomic_write_json(self.path, doc, SETTINGS_SCHEMA)

    def idle_timeout_minutes(self) -> int | None:
        """The saved idle timeout in minutes, None for off, the default if unset."""
        return self._read().get("idle_timeout_minutes", DEFAULT_IDLE_MINUTES)

    def set_idle_timeout_minutes(self, minutes: int | None) -> None:
        self._update(idle_timeout_minutes=minutes)

    def theme(self) -> str:
        """The saved home page theme: "system", "light" or "dark"."""
        return self._read().get("theme", DEFAULT_THEME)

    def set_theme(self, theme: str) -> None:
        self._update(theme=theme)


def minutes_to_seconds(minutes: int | None) -> float | None:
    return None if minutes is None else minutes * 60.0
