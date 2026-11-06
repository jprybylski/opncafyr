"""Internal-mirror mode (#36): fetch every pinned file from the organisation's mirrors.

A ``mirror.toml`` maps each origin prefix to a mirror prefix::

    [mirrors]
    "https://github.com/" = "https://artifacts.example.org/github-releases/"
    "https://files.pythonhosted.org/" = "https://artifacts.example.org/pypi-files/"
    "https://packagemanager.posit.co/cran/2026-11-06/" = "https://artifacts.example.org/cran-snapshot/"
    "https://cran.r-project.org/" = "https://artifacts.example.org/cran/"

Each pinned URL is rewritten by its longest matching prefix; the file name and the pinned
SHA-256 don't change, so a mirror changes only where a file comes from, never what is
accepted. A URL with no configured prefix is refused: mirror mode never falls back to the
public origin. Mirrors must be ``https``. Credentials, if a mirror needs them, come from
the user's ``~/.netrc`` (``_netrc`` on Windows); they never go in this file.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

from opncafyr.installer.verify import InstallError


class MirrorError(InstallError):
    pass


@dataclass(frozen=True)
class Mirror:
    prefixes: tuple[tuple[str, str], ...]

    @classmethod
    def load(cls, path: str | Path) -> Mirror:
        try:
            with open(path, "rb") as fh:
                doc = tomllib.load(fh)
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise MirrorError(f"Can't read the mirror configuration {path}: {exc}") from None
        table = doc.get("mirrors")
        if not isinstance(table, dict) or not table:
            raise MirrorError(f"{path} has no [mirrors] table")
        pairs = []
        for origin, mirror in table.items():
            if not isinstance(mirror, str):
                raise MirrorError(f"{path}: the mirror for {origin} must be a string")
            for label, url in (("origin", origin), ("mirror", mirror)):
                if not url.startswith("https://") or not url.endswith("/"):
                    raise MirrorError(f"{path}: {label} {url!r} must start https:// and end /")
            pairs.append((origin, mirror))
        pairs.sort(key=lambda p: len(p[0]), reverse=True)
        return cls(tuple(pairs))

    def rewrite(self, url: str) -> str:
        for origin, mirror in self.prefixes:
            if url.startswith(origin):
                return mirror + url[len(origin) :]
        raise MirrorError(f"no mirror is configured for {url} (mirror mode never uses the origin)")
