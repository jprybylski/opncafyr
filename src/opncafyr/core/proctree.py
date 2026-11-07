"""Owned process trees: argv-only spawn, an allow-listed environment, and tree kill.

Every child the launcher starts (web apps and jobs) goes through this module:

- ``argv`` is a list whose first item is an absolute path from the pinned runtime. There is
  no shell, ever.
- The environment is built from an allow-list, not inherited. Variables that widen a
  child's Host check, and the launcher's own ``OPNCAFYR_*`` settings, are always dropped.
- The whole tree is killed, not just the direct child. processx and deckifyr ``setsid()``
  their workers, so a process-group kill alone would miss them. On POSIX we freeze and
  walk the descendants; on Windows the child runs in a Job Object.
"""

from __future__ import annotations

import contextlib
import os
import signal
import socket
import subprocess  # noqa: S404 - argv arrays only, see spawn()
import tempfile
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Any

import psutil

IS_WINDOWS = os.name == "nt"

# Re-exported so other modules start processes only through this one.
PIPE = subprocess.PIPE
STDOUT = subprocess.STDOUT
DEVNULL = subprocess.DEVNULL
TimeoutExpired = subprocess.TimeoutExpired

# Variables a child may inherit from the launcher's environment.
_KEEP_POSIX = frozenset({"HOME", "USER", "LOGNAME", "LANG", "TZ"})
_KEEP_WINDOWS = frozenset(
    {
        "SYSTEMROOT",
        "WINDIR",
        "SYSTEMDRIVE",
        "PATHEXT",
        "USERPROFILE",
        "USERNAME",
        "HOMEDRIVE",
        "HOMEPATH",
        "APPDATA",
        "LOCALAPPDATA",
        "PROGRAMDATA",
        "NUMBER_OF_PROCESSORS",
        "PROCESSOR_ARCHITECTURE",
        "LANG",
        "TZ",
    }
)

# Never passed to a child, even if an adapter asks. The first four make opnca-r widen its
# Host allow-list (to "*" when they name no URL).
DENIED_ENV = frozenset(
    {"OPNCA_APP_ALLOWED_HOSTS", "RSTUDIO_PROXY_PATH", "VSCODE_PROXY_URI", "RS_SERVER_URL"}
)
DENIED_ENV_PREFIXES = ("OPNCAFYR_",)


def _denied(name: str) -> bool:
    upper = name.upper()
    return upper in DENIED_ENV or upper.startswith(DENIED_ENV_PREFIXES)


def _kept(name: str) -> bool:
    upper = name.upper()
    if IS_WINDOWS:
        return upper in _KEEP_WINDOWS
    return upper in _KEEP_POSIX or upper.startswith("LC_")


def system_path_dirs() -> list[str]:
    """The minimal system directories appended after the pinned tool directories."""
    if IS_WINDOWS:
        root = os.environ.get("SYSTEMROOT", r"C:\Windows")
        return [rf"{root}\System32", root, rf"{root}\System32\Wbem"]
    return ["/usr/bin", "/bin", "/usr/sbin", "/sbin"]


def child_tmpdir(own: Path, prefix: str) -> tuple[Path, Path | None]:
    """A child's TMPDIR: ``own``, unless its path has whitespace.

    R refuses to start when its temp folder's path contains a space ("'R_TempDir'
    contains space"). Study folders often have one ("PK Study"), and so does the macOS app
    root (``~/Library/Application Support``). Such a child gets a private folder in the
    system temp folder instead. Returns ``(tmpdir, extra)``: ``extra`` is that private
    folder, which the caller removes with the child, or None.
    """
    if any(ch.isspace() for ch in str(own)):
        system = Path(tempfile.gettempdir())
        if not any(ch.isspace() for ch in str(system)):
            extra = Path(tempfile.mkdtemp(prefix=prefix, dir=system))
            return extra, extra
    own.mkdir(parents=True, exist_ok=True)
    return own, None  # (with a space in both, R children report their own error)


def child_env(
    extra: Mapping[str, str] | None = None,
    path_dirs: Iterable[str | Path] = (),
    base: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Build a child's environment from the allow-list plus ``extra``.

    ``PATH`` is always the pinned tool directories followed by the system minimum. Denied
    variables are removed last, so ``extra`` cannot reintroduce them.
    """
    source = os.environ if base is None else base
    env = {k: v for k, v in source.items() if _kept(k)}
    extra = dict(extra or {})
    if any(k.upper() == "PATH" for k in extra):
        raise ValueError("Set PATH through path_dirs, not extra.")
    env.update(extra)
    env["PATH"] = os.pathsep.join([*(str(p) for p in path_dirs), *system_path_dirs()])
    return {k: v for k, v in env.items() if not _denied(k)}


def free_loopback_port() -> int:
    """A currently free loopback port.

    There is a small race between closing this socket and the child binding the port; the
    supervisor retries on a new port when a child exits before it is ready.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@dataclass
class OwnedProcess:
    """A spawned child plus whatever is needed to kill its whole tree."""

    popen: subprocess.Popen[bytes]
    _job: Any = field(default=None, repr=False)

    @property
    def pid(self) -> int:
        return self.popen.pid

    def poll(self) -> int | None:
        return self.popen.poll()

    def wait(self, timeout: float | None = None) -> int:
        return self.popen.wait(timeout)

    def descendants(self) -> list[psutil.Process]:
        try:
            return psutil.Process(self.pid).children(recursive=True)
        except psutil.Error:
            return []

    def kill_tree(self, timeout: float = 10.0, known: Iterable[psutil.Process] = ()) -> None:
        """Kill the child and every descendant, then reap the child.

        ``known`` are descendants seen earlier (for example just before an orderly stop).
        Once their parent exits they are reparented and can't be found by walking the
        tree, so they are killed explicitly. psutil checks each process's start time, so a
        reused pid is never killed. Once the child has exited and been reaped its pid may
        belong to another process, so only ``known`` is walked then.
        """
        exited = self.popen.poll() is not None
        if IS_WINDOWS:
            procs = [*([] if exited else self.descendants()), *known]
            if self._job is not None:
                self._job.terminate()
            _kill_all(procs)
            if not exited:
                self.popen.kill()
        else:
            procs = _freeze_tree(None if exited else self.pid, extra=known)
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(self.pid, signal.SIGKILL)  # start_new_session: pgid == pid
            _kill_all(procs)
        with contextlib.suppress(subprocess.TimeoutExpired):
            self.popen.wait(timeout)
        psutil.wait_procs(procs, timeout=timeout)
        if self._job is not None:
            self._job.close()
            self._job = None


def _kill_all(procs: Iterable[psutil.Process]) -> None:
    for proc in procs:
        with contextlib.suppress(psutil.Error):
            proc.kill()


def _freeze_tree(
    root_pid: int | None, rounds: int = 10, extra: Iterable[psutil.Process] = ()
) -> list[psutil.Process]:
    """SIGSTOP the root, ``extra`` and all their descendants until no new ones appear."""
    seen: dict[int, psutil.Process] = {}
    roots = [p for p in extra if p.is_running()]
    if root_pid is not None:
        with contextlib.suppress(psutil.Error):
            roots.insert(0, psutil.Process(root_pid))
    for _ in range(rounds):
        current: list[psutil.Process] = []
        for root in roots:
            current.append(root)
            with contextlib.suppress(psutil.Error):
                current.extend(root.children(recursive=True))
        fresh = [p for p in current if p.pid not in seen]
        if not fresh:
            break
        for proc in fresh:
            seen[proc.pid] = proc
            with contextlib.suppress(psutil.Error):
                proc.suspend()
    return list(seen.values())


def kill_recorded_tree(pid: int, create_time: float, timeout: float = 10.0) -> bool:
    """Kill a tree that an earlier launcher started and recorded, if it is still running.

    ``create_time`` is the psutil start time recorded at spawn. A pid now held by a
    different process is left alone. Returns True if the recorded process was found.
    A grandchild whose parent has already exited can't be found from here; on Windows the
    Job Object killed the whole tree when the old launcher's handle closed.
    """
    try:
        proc = psutil.Process(pid)
        if abs(proc.create_time() - create_time) > 0.5:
            return False
    except psutil.Error:
        return False
    if IS_WINDOWS:
        procs = [proc]
        with contextlib.suppress(psutil.Error):
            procs.extend(proc.children(recursive=True))
    else:
        procs = _freeze_tree(pid)
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(pid, signal.SIGKILL)  # started with start_new_session: pgid == pid
    _kill_all(procs)
    psutil.wait_procs(procs, timeout=timeout)
    return True


def spawn(
    argv: Sequence[str],
    *,
    env: Mapping[str, str],
    cwd: str | Path,
    stdout: int | IO[bytes] | None = subprocess.PIPE,
    stderr: int | IO[bytes] | None = subprocess.STDOUT,
) -> OwnedProcess:
    """Start ``argv`` (no shell) in its own session or Job Object."""
    args = [str(a) for a in argv]
    if not args or not Path(args[0]).is_absolute():
        raise ValueError("argv[0] must be an absolute path from the pinned runtime.")
    if not Path(args[0]).is_file():
        raise FileNotFoundError(f"Executable not found: {args[0]}")
    common: dict[str, Any] = {
        "env": dict(env),
        "cwd": str(cwd),
        "stdin": subprocess.DEVNULL,
        "stdout": stdout,
        "stderr": stderr,
        "shell": False,
        "close_fds": True,
    }
    if IS_WINDOWS:
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        popen = subprocess.Popen(args, creationflags=flags, **common)  # noqa: S603
        job = _WindowsJob()
        try:
            # The child can start a grandchild before this line runs. That window is tiny
            # (our children are interpreters that take far longer to start) and documented.
            job.assign(popen)
        except OSError:
            job.close()
            popen.kill()
            raise
        return OwnedProcess(popen, job)
    popen = subprocess.Popen(args, start_new_session=True, **common)  # noqa: S603
    return OwnedProcess(popen)


class _WindowsJob:
    """A Job Object that kills every process in it when terminated or closed."""

    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes

        class BasicLimits(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class IoCounters(ctypes.Structure):
            _fields_ = [
                (name, ctypes.c_uint64)
                for name in (
                    "ReadOperationCount",
                    "WriteOperationCount",
                    "OtherOperationCount",
                    "ReadTransferCount",
                    "WriteTransferCount",
                    "OtherTransferCount",
                )
            ]

        class ExtendedLimits(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", BasicLimits),
                ("IoInfo", IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
        k32.CreateJobObjectW.restype = wintypes.HANDLE
        k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        k32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        k32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        self._ctypes = ctypes
        self._k32 = k32
        handle = k32.CreateJobObjectW(None, None)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())  # type: ignore[attr-defined]
        self._handle = handle
        limits = ExtendedLimits()
        limits.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        ok = k32.SetInformationJobObject(
            handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)
        )  # 9 = JobObjectExtendedLimitInformation
        if not ok:
            err = ctypes.get_last_error()  # type: ignore[attr-defined]
            self.close()
            raise ctypes.WinError(err)  # type: ignore[attr-defined]

    def assign(self, popen: subprocess.Popen[bytes]) -> None:
        handle = int(popen._handle)  # type: ignore[attr-defined]
        if not self._k32.AssignProcessToJobObject(self._handle, handle):
            raise self._ctypes.WinError(self._ctypes.get_last_error())

    def terminate(self) -> None:
        if self._handle:
            self._k32.TerminateJobObject(self._handle, 1)

    def close(self) -> None:
        if self._handle:
            self._k32.CloseHandle(self._handle)
            self._handle = None


__all__ = [
    "DENIED_ENV",
    "IS_WINDOWS",
    "OwnedProcess",
    "child_env",
    "free_loopback_port",
    "kill_recorded_tree",
    "spawn",
    "system_path_dirs",
]
