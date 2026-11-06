"""The installer payload: the wrappers, the opncafyr wheel and the pins it installs.

``scripts/make_installer.py`` builds it::

    install.sh  install.bat  install.ps1  run_installer.py  bootstrap.tsv  payload.json
    opncafyr-<version>-py3-none-any.whl
    runtime/pins.toml  runtime/r/renv.lock  runtime/r/binaries.toml  runtime/python/wheels.toml

``payload.json`` (schema ``payload-v1``) lists every other file with its SHA-256. The
payload digest (the SHA-256 of that list) identifies exactly what an install was made
from; the installation report prints it so IT can compare it with a published value
(#36). Until #39 signs the payload, the pins inside it are the trust root.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path

from opncafyr.installer.verify import InstallError, sha256_bytes, sha256_file

PAYLOAD_FILE = "payload.json"


@dataclass(frozen=True)
class Payload:
    directory: Path
    version: str
    files: dict[str, str]
    digest: str

    @property
    def wheel(self) -> Path:
        return self.directory / next(f for f in self.files if f.endswith(".whl"))

    @property
    def runtime(self) -> Path:
        return self.directory / "runtime"

    def copy_to(self, dest: Path) -> None:
        """Copy the payload (for repair and uninstall later) into ``dest``."""
        tmp = dest.with_name(dest.name + ".tmp")
        if tmp.exists():
            shutil.rmtree(tmp)
        for rel in [*self.files, PAYLOAD_FILE]:
            target = tmp / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(self.directory / rel, target)
        if dest.exists():
            shutil.rmtree(dest)
        tmp.rename(dest)


def digest_of(files: dict[str, str]) -> str:
    return sha256_bytes(json.dumps(files, sort_keys=True, separators=(",", ":")).encode())


def load(directory: str | Path, *, verify: bool = True) -> Payload:
    directory = Path(directory).resolve()
    try:
        doc = json.loads((directory / PAYLOAD_FILE).read_text("utf-8"))
        files = {str(k): str(v) for k, v in doc["files"].items()}
        version = str(doc["version"])
    except (OSError, ValueError, KeyError, AttributeError) as exc:
        raise InstallError(
            f"{directory} is not an installer payload ({PAYLOAD_FILE}: {exc}). Build one with "
            "`uv run python scripts/make_installer.py`."
        ) from None
    if digest_of(files) != doc.get("digest"):
        raise InstallError(f"{PAYLOAD_FILE} is damaged: its digest does not match its files.")
    if verify:
        for rel, sha in files.items():
            path = directory / rel
            if not path.is_file() or sha256_file(path) != sha:
                raise InstallError(
                    f"The installer payload is damaged: {rel} is missing or changed. "
                    "Download the installer again."
                )
    if sum(f.endswith(".whl") for f in files) != 1:
        raise InstallError("The installer payload must hold exactly one opncafyr wheel.")
    return Payload(directory, version, files, doc["digest"])
