"""The installer's only way to start a process.

Every child gets an argv list with an absolute executable (never a shell) and an
allow-listed environment built here, not the user's: a user's ``R_LIBS``, ``PYTHONPATH``,
``UV_INDEX_URL`` or ``.Rprofile`` can't leak into an install. The offline settings from
docs/offline.md are always on; in bundle mode the proxy variables point at a closed
loopback port, so a tool that still tries the network fails at once instead of
connecting (#36).

``core.proctree`` does the same for the launcher, but it needs psutil, which the bare
installer interpreter doesn't have.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from pathlib import Path

NULL_DEVICE = "NUL" if sys.platform == "win32" else "/dev/null"
# Passed through from the user's environment.
_KEEP_WINDOWS = (
    "SYSTEMROOT",
    "SYSTEMDRIVE",
    "WINDIR",
    "COMSPEC",
    "PATHEXT",
    "USERPROFILE",
    "USERNAME",
    "USERDOMAIN",
    "HOMEDRIVE",
    "HOMEPATH",
    "LOCALAPPDATA",
    "APPDATA",
    "PROGRAMDATA",
    "PROGRAMFILES",
    "PROGRAMFILES(X86)",
    "COMMONPROGRAMFILES",
    "PROCESSOR_ARCHITECTURE",
    "NUMBER_OF_PROCESSORS",
    "OS",
    "TEMP",
    "TMP",
)
_KEEP_POSIX = ("HOME", "USER", "LOGNAME")
_PROXY = ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "NO_PROXY", "no_proxy")
_PROXY += ("ALL_PROXY", "all_proxy", "CURL_CA_BUNDLE", "SSL_CERT_FILE")
CLOSED_PROXY = "http://127.0.0.1:9"
# CI only: "0" turns the closed proxy off, so a stray attempt reaches the network block
# (sandbox, firewall) and is logged there, instead of failing quietly on loopback.
PROXY_TRAP_ENV = "OPNCAFYR_PROXY_TRAP"


class ProcError(RuntimeError):
    def __init__(self, message: str, *, returncode: int | None = None, tail: str = "") -> None:
        super().__init__(message)
        self.returncode = returncode
        self.tail = tail


def system_path() -> str:
    if sys.platform == "win32":
        root = os.environ.get("SYSTEMROOT", r"C:\Windows")
        return os.pathsep.join(
            [rf"{root}\System32", root, rf"{root}\System32\WindowsPowerShell\v1.0"]
        )
    return "/usr/bin:/bin:/usr/sbin:/sbin"


def base_env(
    *,
    offline: bool,
    tmpdir: Path | None = None,
    cache: Path | None = None,
    path_prepend: Sequence[Path] = (),
    environ: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """The allow-listed environment for installer children."""
    src = os.environ if environ is None else environ
    keep = _KEEP_WINDOWS if sys.platform == "win32" else _KEEP_POSIX
    env = {k: src[k] for k in keep if k in src}
    env["PATH"] = os.pathsep.join([*(str(p) for p in path_prepend), system_path()])
    if sys.platform.startswith("linux"):
        # en_US.UTF-8 is often not generated on a minimal VM; C.UTF-8 always exists (#65).
        env["LANG"] = env["LC_ALL"] = "C.UTF-8"
    elif sys.platform != "win32":
        env["LANG"] = env["LC_ALL"] = "en_US.UTF-8"
    if tmpdir is not None:
        tmpdir.mkdir(parents=True, exist_ok=True)
        for key in ("TMPDIR", "TEMP", "TMP"):
            env[key] = str(tmpdir)
    # uv: never resolve, sync or download Python behind our back (docs/offline.md).
    env |= {
        "UV_OFFLINE": "1",
        "UV_NO_INDEX": "1",
        "UV_NO_CONFIG": "1",
        "UV_NO_SYNC": "1",
        "UV_PYTHON_DOWNLOADS": "never",
        "UV_LINK_MODE": "copy",
        "UV_NO_PROGRESS": "1",
    }
    if cache is not None:
        env["UV_CACHE_DIR"] = str(cache / "uv")
        env["DENO_DIR"] = str(cache / "deno")
        env["XDG_CACHE_HOME"] = str(cache / "xdg")
    # R: no user or site profile, no site library, no renv bootstrap or update check.
    env |= {
        "R_ENVIRON_USER": NULL_DEVICE,
        "R_PROFILE_USER": NULL_DEVICE,
        "R_LIBS_SITE": "",
        "RENV_CONFIG_AUTOLOADER_ENABLED": "FALSE",
        "RENV_CONFIG_UPDATES_CHECK": "FALSE",
    }
    if offline and src.get(PROXY_TRAP_ENV) == "0":
        pass
    elif offline:
        for key in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
            env[key] = CLOSED_PROXY
        for key in ("ALL_PROXY", "all_proxy"):
            env[key] = CLOSED_PROXY
        env["NO_PROXY"] = env["no_proxy"] = ""
    else:
        env |= {k: src[k] for k in _PROXY if k in src}
    return env


def space_free_tmp(tag: str, fallback: Path) -> Path:
    """A temporary folder for children. R refuses one whose path contains a space, and on
    macOS the app root (Application Support) has one, so use the per-user temporary folder
    macOS provides (``/var/folders/…``) there."""
    if sys.platform == "darwin":
        done = capture(["/usr/bin/getconf", "DARWIN_USER_TEMP_DIR"], env=base_env(offline=True))
        base = done.stdout.decode().strip()
        if done.returncode == 0 and base and " " not in base:
            return Path(base) / f"opncafyr-{tag}"
    return fallback


def run(
    argv: Sequence[str | Path],
    *,
    env: Mapping[str, str],
    log: Path,
    cwd: Path | None = None,
    timeout: float | None = None,
    stdin: bytes | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[bytes]:
    """Run ``argv`` with output appended to ``log``. Judged by exit code only."""
    args = [str(a) for a in argv]
    if not args or not Path(args[0]).is_absolute():
        raise ValueError(f"Executables must be absolute paths: {args[:1]}")
    log.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    with log.open("ab") as out:
        out.write(f"\n$ {_show(args)}\n".encode())
        out.flush()
        try:
            done = subprocess.run(  # noqa: S603 - argv list, absolute executable, no shell
                args,
                env=dict(env),
                cwd=str(cwd) if cwd else None,
                stdout=out,
                stderr=subprocess.STDOUT,
                input=stdin,
                timeout=timeout,
                check=False,
                shell=False,
                creationflags=_no_window(),
            )
        except subprocess.TimeoutExpired:
            raise ProcError(f"{Path(args[0]).name} timed out after {timeout:.0f} s") from None
        except OSError as exc:
            raise ProcError(f"{Path(args[0]).name} could not start: {exc}") from exc
        out.write(f"[exit {done.returncode} after {time.monotonic() - started:.1f} s]\n".encode())
    if check and done.returncode != 0:
        raise ProcError(
            f"{Path(args[0]).name} exited with {done.returncode} (see {log.name})",
            returncode=done.returncode,
            tail=tail(log),
        )
    return done


def capture(
    argv: Sequence[str | Path],
    *,
    env: Mapping[str, str],
    cwd: Path | None = None,
    timeout: float | None = 120,
    stdin: bytes | None = None,
) -> subprocess.CompletedProcess[bytes]:
    """Run ``argv`` and return its output (for short queries, never long installs)."""
    args = [str(a) for a in argv]
    if not args or not Path(args[0]).is_absolute():
        raise ValueError(f"Executables must be absolute paths: {args[:1]}")
    try:
        return subprocess.run(  # noqa: S603 - argv list, absolute executable, no shell
            args,
            env=dict(env),
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            input=stdin,
            timeout=timeout,
            check=False,
            shell=False,
            creationflags=_no_window(),
        )
    except subprocess.TimeoutExpired:
        raise ProcError(f"{Path(args[0]).name} timed out") from None
    except OSError as exc:
        raise ProcError(f"{Path(args[0]).name} could not start: {exc}") from exc


def tail(log: Path, lines: int = 25) -> str:
    try:
        text = log.read_text("utf-8", errors="replace")
    except OSError:
        return ""
    return "\n".join(text.splitlines()[-lines:])


def _show(args: list[str]) -> str:
    return " ".join(f'"{a}"' if " " in a else a for a in args)


def _no_window() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
