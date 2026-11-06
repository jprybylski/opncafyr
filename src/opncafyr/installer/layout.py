"""Where an install lives, and the ``current`` pointer that activates one.

```
<app root>/                       %LOCALAPPDATA%\\opncafyr, ~/Library/Application Support/opncafyr
  current                         symlink to versions/<id> (macOS) or a file naming <id> (Windows)
  versions/<id>/                  one complete install; complete only once install.json exists
  installer/                      copy of the active installer payload (repair, uninstall)
  state/  logs/  cache/
~/Library/opncafyr/versions/<id>/ macOS only: R.framework (R can't live under a path with a space)
```

An install is written in place under ``versions/<id>``, because R, the venvs and
``runtime.json`` record absolute paths; renaming a staged folder would break them. A folder
without ``install.json`` is incomplete: it is never activated, and the next run removes it.
Activation is one atomic step: ``os.replace`` of the symlink or pointer file.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import sys
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import IO, Any

APP_NAME = "opncafyr"
APP_ROOT_ENV = "OPNCAFYR_APP_ROOT"  # tests and CI only: a different app root
R_ROOT_ENV = "OPNCAFYR_R_ROOT"  # tests and CI only: a different macOS R root
INSTALL_RECORD = "install.json"
CURRENT = "current"
_ID = re.compile(r"^(?P<version>[0-9A-Za-z.+]+)-(?P<n>[0-9]+)$")


def default_app_root(environ: Mapping[str, str] | None = None) -> Path:
    """The per-user app root; the same as ``opncafyr.paths.app_root()`` (tested)."""
    env = os.environ if environ is None else environ
    if env.get(APP_ROOT_ENV):
        return Path(env[APP_ROOT_ENV])
    if sys.platform == "win32":
        base = env.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / APP_NAME
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    xdg = env.get("XDG_DATA_HOME", "").strip()
    return Path(xdg if xdg else Path.home() / ".local" / "share") / APP_NAME


def default_r_root(environ: Mapping[str, str] | None = None) -> Path:
    """macOS: R's own root. It must not contain a space (docs/spikes/macos-install.md)."""
    env = os.environ if environ is None else environ
    if env.get(R_ROOT_ENV):
        return Path(env[R_ROOT_ENV])
    return Path.home() / "Library" / APP_NAME


def installed_root(prefix: str | Path | None = None) -> Path | None:
    """The app root of the install this interpreter belongs to, if it is one.

    A launcher venv lives at ``<root>/versions/<id>/launcher``. Paths are resolved first,
    so a start through the ``current`` symlink still finds its own install.
    """
    try:
        here = Path(sys.prefix if prefix is None else prefix).resolve()
    except OSError:
        return None
    for parent in [here, *here.parents]:
        if (parent / INSTALL_RECORD).is_file() and parent.parent.name == "versions":
            return parent.parent.parent
    return None


def installed_version_dir(prefix: str | Path | None = None) -> Path | None:
    """``versions/<id>`` of the install this interpreter belongs to, if any."""
    try:
        here = Path(sys.prefix if prefix is None else prefix).resolve()
    except OSError:
        return None
    for parent in [here, *here.parents]:
        if (parent / INSTALL_RECORD).is_file() and parent.parent.name == "versions":
            return parent
    return None


class Layout:
    def __init__(self, root: Path, r_root: Path | None = None) -> None:
        self.root = Path(root)
        self.r_root = Path(r_root) if r_root is not None else None

    @classmethod
    def default(cls) -> Layout:
        return cls(default_app_root(), default_r_root() if sys.platform == "darwin" else None)

    # --- folders -------------------------------------------------------------------
    @property
    def versions(self) -> Path:
        return self.root / "versions"

    @property
    def state(self) -> Path:
        return self.root / "state"

    @property
    def logs(self) -> Path:
        return self.root / "logs"

    @property
    def cache(self) -> Path:
        return self.root / "cache"

    @property
    def downloads(self) -> Path:
        return self.cache / "downloads"

    @property
    def installer_copy(self) -> Path:
        return self.root / "installer"

    @property
    def current(self) -> Path:
        return self.root / CURRENT

    def version_dir(self, install_id: str) -> Path:
        return self.versions / install_id

    def r_dir(self, install_id: str) -> Path:
        """Where R itself goes for an install."""
        if self.r_root is not None:
            return self.r_root / "versions" / install_id
        return self.version_dir(install_id) / "r"

    # --- installs ------------------------------------------------------------------
    def record(self, install_id: str) -> dict[str, Any] | None:
        path = self.version_dir(install_id) / INSTALL_RECORD
        try:
            doc = json.loads(path.read_text("utf-8"))
        except (OSError, ValueError):
            return None
        return doc if isinstance(doc, dict) and doc.get("status") == "complete" else None

    def install_ids(self) -> list[str]:
        if not self.versions.is_dir():
            return []
        return sorted(p.name for p in self.versions.iterdir() if p.is_dir() and _ID.match(p.name))

    def complete_ids(self) -> list[str]:
        return [i for i in self.install_ids() if self.record(i) is not None]

    def incomplete_ids(self) -> list[str]:
        return [i for i in self.install_ids() if self.record(i) is None]

    def next_install_id(self, version: str) -> str:
        if not re.fullmatch(r"[0-9A-Za-z.+]+", version):
            raise ValueError(f"Unusable version {version!r}")
        taken = set(self.install_ids())
        if self.r_root is not None and (self.r_root / "versions").is_dir():
            taken |= {p.name for p in (self.r_root / "versions").iterdir()}
        numbers = [int(m["n"]) for t in taken if (m := _ID.match(t))]
        return f"{version}-{max(numbers, default=0) + 1}"

    # --- current -------------------------------------------------------------------
    def read_current(self) -> str | None:
        """The active install id, or None. A pointer to an incomplete install counts as none."""
        path = self.current
        try:
            if path.is_symlink():
                name = Path(os.readlink(path)).name
            elif path.is_file():
                name = path.read_text("utf-8").strip()
            else:
                return None
        except OSError:
            return None
        return name if _ID.match(name) and self.record(name) is not None else None

    def write_current(self, install_id: str) -> None:
        """Atomically make ``install_id`` the active install."""
        if self.record(install_id) is None:
            raise ValueError(f"{install_id} is not a complete install")
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = self.root / f".{CURRENT}.{os.getpid()}.tmp"
        with contextlib.suppress(FileNotFoundError):
            tmp.unlink()
        if sys.platform == "win32":
            tmp.write_text(install_id + "\n", "utf-8")
        else:
            os.symlink(Path("versions") / install_id, tmp)
        os.replace(tmp, self.current)

    def remove_install(self, install_id: str) -> None:
        """Delete one install (its folder and, on macOS, its R). Never follows symlinks."""
        for path in (self.version_dir(install_id), self.r_dir(install_id)):
            remove_tree(path)

    # --- locks ---------------------------------------------------------------------
    @contextlib.contextmanager
    def install_lock(self) -> Iterator[None]:
        """Only one installer at a time. An OS lock, so a crash never leaves it held."""
        self.state.mkdir(parents=True, exist_ok=True)
        fh = (self.state / "install.lock").open("a+b")
        try:
            try:
                _lock(fh)
            except OSError:
                raise InstallerBusy(
                    "Another opncafyr installer is running. Wait for it to finish."
                ) from None
            try:
                yield
            finally:
                with contextlib.suppress(OSError):
                    _unlock(fh)
        finally:
            fh.close()

    def launcher_running(self) -> bool:
        """True if a launcher holds ``state/instance.lock`` (see ``core.instance``)."""
        path = self.state / "instance.lock"
        if not path.exists():
            return False
        try:
            fh = path.open("a+b")
        except OSError:
            return True
        try:
            try:
                _lock(fh)
            except OSError:
                return True
            with contextlib.suppress(OSError):
                _unlock(fh)
            return False
        finally:
            fh.close()


class InstallerBusy(RuntimeError):
    pass


def remove_tree(path: Path) -> None:
    """Remove a file, link or folder without following links out of it."""
    if path.is_symlink() or path.is_file():
        path.unlink()
        return
    if not path.exists():
        return

    def onexc(func: Any, p: str, exc: BaseException) -> None:
        # Read-only files (R's installer marks some) can't be removed on Windows.
        if isinstance(exc, PermissionError):
            os.chmod(p, 0o700)
            func(p)
        else:
            raise exc

    shutil.rmtree(path, onexc=onexc)


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
