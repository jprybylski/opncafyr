"""Every pinned file an install needs on one platform, from the pins and the locks.

Sources (all under the payload's ``runtime/``): ``pins.toml``, ``r/renv.lock``,
``r/binaries.toml`` and ``python/wheels.toml``. Each file becomes an ``Asset`` with its
component, version, SHA-256, source URL and licence. The SHA-256 here is the trust root:
the installer never takes a hash from a bundle or a download.
"""

from __future__ import annotations

import json
import tomllib
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from opncafyr.installer.platforms import SUPPORTED
from opncafyr.installer.verify import InstallError

# R packages that don't come from CRAN; their tarballs go in the cellar.
CELLAR = ("quartifyr", "reportifyr", "pyro")
OPNCA = "opnca-r"
GIT = "git"  # [optional.git]: the portable Git for version history (#56)


class PinsError(InstallError):
    pass


@dataclass(frozen=True)
class Asset:
    component: str
    name: str
    file: str
    url: str
    sha256: str
    version: str = ""
    license: str = ""
    private: bool = False
    optional: bool = False
    # An optional component installed by default (``default = true``, e.g. Git, #56): if
    # it can't be fetched or installed, the install goes on without it.
    best_effort: bool = False

    @property
    def path(self) -> str:
        """Where the file sits in a bundle: ``assets/<component>/<file>``."""
        return f"assets/{self.component}/{self.file}"


@dataclass
class Pins:
    runtime_dir: Path
    pins: dict[str, Any] = field(repr=False)
    lock: dict[str, Any] = field(repr=False)
    binaries: dict[str, Any] = field(repr=False)
    wheels: dict[str, Any] = field(repr=False)

    @classmethod
    def load(cls, runtime_dir: str | Path) -> Pins:
        runtime_dir = Path(runtime_dir)
        try:
            pins = _toml(runtime_dir / "pins.toml")
            section = pins["r_packages"]
            lock = json.loads(
                (runtime_dir / Path(section["lockfile"]).relative_to("runtime")).read_text("utf-8")
            )
            binaries = _toml(runtime_dir / Path(section["binaries"]).relative_to("runtime"))
            wheels = _toml(runtime_dir / "python" / "wheels.toml")
        except (OSError, KeyError, ValueError) as exc:
            raise PinsError(f"Can't read the pins in {runtime_dir}: {exc}") from exc
        return cls(runtime_dir, pins, lock, binaries, wheels)

    # --- versions ------------------------------------------------------------------
    def version(self, component: str) -> str:
        if component in self.pins:
            return str(self.pins[component].get("version", ""))
        return str(self.pins["packages"][component]["version"])

    @property
    def python_version(self) -> str:
        return str(self.pins["python"]["version"])

    @property
    def r_version(self) -> str:
        return str(self.pins["r"]["version"])

    def lock_records(self) -> dict[str, dict[str, Any]]:
        return dict(self.lock["Packages"])

    def wheel_list(self, env: str, platform: str) -> list[dict[str, str]]:
        try:
            return list(self.wheels[env][platform])
        except KeyError:
            raise PinsError(f"wheels.toml has no [{env}.{platform}] list") from None

    # --- assets --------------------------------------------------------------------
    def assets(
        self,
        platform: str,
        *,
        libreoffice: bool = False,
        git: bool = True,
        source_build: bool = False,
    ) -> list[Asset]:
        """Every pinned file an install of ``platform`` needs.

        ``git`` adds the pinned portable Git (``[optional.git]``, #56) when the pins have
        it, as a best-effort asset. ``source_build`` adds what ``repair
        --build-from-source`` needs (#34): opnca-r's vendored source tarball, the
        standalone Rust toolchain and, on Windows, Rtools.
        """
        if platform not in SUPPORTED:
            raise PinsError(f"Unsupported platform {platform}")
        out = [self._runtime(name, platform) for name in ("uv", "python", "r", "quarto")]
        out += self._opnca(platform)
        out += self._cellar()
        out += self._r_binaries(platform)
        out += self._wheels(platform)
        if libreoffice:
            out.append(self._runtime("libreoffice", platform, section=("optional",)))
        if git and GIT in self.pins.get("optional", {}):
            out.append(self._runtime(GIT, platform, section=("optional",)))
        if source_build:
            out += self._source_build(platform)
        seen: dict[str, Asset] = {}
        for asset in out:
            other = seen.get(asset.path)
            if other is not None and other.sha256 != asset.sha256:
                raise PinsError(f"{asset.path} is pinned twice with different hashes")
            seen[asset.path] = asset
        return list(seen.values())

    def _runtime(self, name: str, platform: str, section: tuple[str, ...] = ()) -> Asset:
        node: Any = self.pins
        for key in (*section, name):
            node = node[key]
        entry = node["assets"][platform]
        url = entry["url"]
        return Asset(
            component=name,
            name=name,
            file=_filename(url),
            url=url,
            sha256=entry["sha256"],
            version=str(node.get("version", "")),
            license=str(node.get("license", "")),
            optional=bool(section),
            best_effort=bool(section) and node.get("default") is True,
        )

    def _opnca(self, platform: str) -> list[Asset]:
        pin = self.pins["packages"][OPNCA]
        binary = [f for f in pin["assets"] if f"_{platform}." in f]
        if len(binary) != 1:
            raise PinsError(f"Expected one opnca-r binary for {platform}, found {binary}")
        return [
            Asset(
                OPNCA,
                OPNCA,
                f,
                pin["release_url"] + f,
                pin["assets"][f],
                str(pin["version"]),
                str(pin.get("license", "")),
                private=bool(pin.get("private", False)),
            )
            for f in ("manifest.json", binary[0])
        ]

    def _source_build(self, platform: str) -> list[Asset]:
        pin = self.pins["packages"][OPNCA]
        vendored = [f for f in pin["assets"] if f.endswith("-vendored.tar.gz")]
        if len(vendored) != 1:
            raise PinsError(f"Expected one vendored opnca-r tarball, found {vendored}")
        out = [
            Asset(
                OPNCA,
                OPNCA,
                vendored[0],
                pin["release_url"] + vendored[0],
                pin["assets"][vendored[0]],
                str(pin["version"]),
                str(pin.get("license", "")),
                private=bool(pin.get("private", False)),
            )
        ]
        rust = self.pins["rust"]
        entry = rust["standalone"]["assets"][platform]
        out.append(
            Asset(
                "rust",
                "rust",
                _filename(entry["url"]),
                entry["url"],
                entry["sha256"],
                str(rust["toolchain"]),
                str(rust["standalone"].get("license", "")),
            )
        )
        if platform in self.pins["rtools"].get("platforms", []):
            out.append(self._runtime("rtools", platform))
        return out

    def _cellar(self) -> list[Asset]:
        out = []
        for key in CELLAR:
            pin = self.pins["packages"][key]
            f = f"{key}_{pin['version']}.tar.gz"
            out.append(
                Asset(
                    key,
                    key,
                    f,
                    pin["release_url"] + f,
                    pin["assets"][f],
                    str(pin["version"]),
                    str(pin.get("license", "")),
                )
            )
        return out

    def _r_binaries(self, platform: str) -> list[Asset]:
        out = []
        for name, record in sorted(self.lock["Packages"].items()):
            if record.get("Source") != "Repository":
                continue
            entry = self.binaries["packages"][name][platform]
            if entry["version"] != record["Version"]:
                raise PinsError(f"binaries.toml {name} is {entry['version']}, not locked")
            out.append(
                Asset(
                    "r-packages",
                    name,
                    _filename(entry["url"]),
                    entry["url"],
                    entry["sha256"],
                    entry["version"],
                )
            )
        return out

    def _wheels(self, platform: str) -> list[Asset]:
        out = []
        for env in ("launcher", "tools"):
            for w in self.wheel_list(env, platform):
                out.append(
                    Asset(
                        "wheels",
                        w["name"],
                        w["file"],
                        w["url"],
                        w["sha256"],
                        w["version"],
                        str(self.pins["packages"]["deckifyr"].get("license", ""))
                        if w["name"] == "deckifyr"
                        else "",
                    )
                )
        return out


def _toml(path: Path) -> dict[str, Any]:
    with path.open("rb") as fh:
        return tomllib.load(fh)


def _filename(url: str) -> str:
    return urllib.parse.unquote(url.rsplit("/", 1)[1])
