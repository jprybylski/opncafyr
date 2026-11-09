"""Unpack verified archives without shelling out, and without escaping the target folder."""

from __future__ import annotations

import io
import os
import shutil
import tarfile
import zipfile
from pathlib import Path

AR_MAGIC = b"!<arch>\n"


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


def deb_data(deb: Path) -> tarfile.TarFile:
    """The ``data.tar.*`` inside a ``.deb`` (an ``ar`` archive), read without ``dpkg``.

    Only gzip, xz and bzip2 are read (the standard library's); a zstd payload is refused with a
    plain message, since a standard user may have no ``unzstd``.
    """
    blob = deb.read_bytes()
    if not blob.startswith(AR_MAGIC):
        raise ValueError(f"{deb.name} is not a .deb (no ar header)")
    pos = len(AR_MAGIC)
    while pos + 60 <= len(blob):
        header = blob[pos : pos + 60]
        name = header[:16].decode("ascii", "replace").strip().rstrip("/")
        size = int(header[48:58].decode("ascii").strip())
        body = blob[pos + 60 : pos + 60 + size]
        if name.startswith("data.tar"):
            if name.endswith(".zst"):
                raise ValueError(f"{deb.name} is zstd-compressed; this installer reads gz and xz")
            return tarfile.open(fileobj=io.BytesIO(body))
        pos += 60 + size + (size & 1)  # members are padded to an even length
    raise ValueError(f"{deb.name} has no data.tar member")


def extract_deb_tree(deb: Path, prefix: str, dest: Path) -> int:
    """Unpack the files under ``prefix`` (``opt/R/4.6.2``) of a ``.deb`` into ``dest``.

    Links are refused, and so are paths that leave ``dest``: Posit's R package holds plain
    files only, so anything else means it is not the file that was pinned. Returns the number
    of files written.
    """
    dest.mkdir(parents=True, exist_ok=True)
    root = dest.resolve()
    wanted = prefix.strip("/") + "/"
    written = 0
    with deb_data(deb) as tar:
        for member in tar:
            name = member.name.removeprefix("./")
            if not name.startswith(wanted) and name != wanted.rstrip("/"):
                continue
            rel = name[len(wanted) :]
            if not rel:
                continue
            if member.issym() or member.islnk() or not (member.isfile() or member.isdir()):
                raise ValueError(f"{deb.name}: {name} is a link or special file")
            target = (dest / rel).resolve()
            if root not in target.parents:
                raise ValueError(f"{deb.name}: {name} escapes the target")
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            source = tar.extractfile(member)
            if source is None:
                raise ValueError(f"{deb.name}: {name} has no content")
            with source, target.open("wb") as out:
                shutil.copyfileobj(source, out)
            target.chmod(0o755 if member.mode & 0o111 else 0o644)
            written += 1
    return written
