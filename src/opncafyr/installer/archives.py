"""Unpack verified archives without shelling out, and without escaping the target folder."""

from __future__ import annotations

import os
import shutil
import tarfile
import zipfile
from pathlib import Path


def extract(archive: Path, dest: Path) -> Path:
    """Unpack a ``.tar.gz``/``.tgz``/``.tar.xz`` or ``.zip`` into ``dest`` (created)."""
    dest.mkdir(parents=True, exist_ok=True)
    name = archive.name.lower()
    if name.endswith(".zip"):
        with zipfile.ZipFile(archive) as zf:
            root = dest.resolve()
            for member in zf.infolist():
                target = (dest / member.filename).resolve()
                if target != root and root not in target.parents:
                    raise ValueError(f"{archive.name}: {member.filename} escapes the target")
            zf.extractall(dest)  # noqa: S202 - every member checked above
    elif name.endswith((".tar.gz", ".tgz", ".tar.xz", ".tar")):
        with tarfile.open(archive) as tf:
            tf.extractall(dest, filter="data")
    else:
        raise ValueError(f"Unknown archive type: {archive.name}")
    return dest


def find_one(root: Path, relative: str) -> Path:
    """The single file under ``root`` whose path ends with ``relative`` (e.g. ``bin/quarto``)."""
    parts = Path(relative).parts
    hits = [
        p
        for p in root.rglob(parts[-1])
        if p.is_file() and p.parts[-len(parts) :] == parts and not p.is_symlink()
    ]
    if len(hits) != 1:
        raise FileNotFoundError(f"expected one {relative} under {root}, found {len(hits)}")
    return hits[0]


def link_or_copy(src: Path, dest: Path) -> None:
    """Hard-link ``src`` to ``dest`` (no admin needed), or copy if that fails."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        dest.unlink()
    try:
        os.link(src, dest)
    except OSError:
        shutil.copyfile(src, dest)
