"""Can a study folder's filesystem keep the workspace's files consistent? (#42, #17)

Manifests, run saves and staged outputs rely on an atomic rename: a new file is written
beside the old one and ``os.replace`` swaps it in, so a reader never sees half a file and
an interrupted write never leaves an incomplete folder that looks complete
(``core.manifest.atomic_write_json``). Object-storage FUSE mounts (s3fs, gcsfuse,
blobfuse, goofys, mountpoint-s3, rclone) fake rename as copy-then-delete, so they are
refused. Network file shares (NFS such as EFS or Filestore, SMB such as Azure Files)
rename atomically within a folder; they are allowed once a rename probe passes, with a
note. Local disks need no probe.

The filesystem type comes from ``/proc/self/mountinfo`` (Linux, including the
container). Other platforms report ``local``: desktop study folders are handled by the
OS, and cloud-synced folders there are covered by the user guide.
"""

from __future__ import annotations

import contextlib
import os
import uuid
from dataclasses import dataclass
from pathlib import Path

MOUNTINFO = Path("/proc/self/mountinfo")

# Filesystem types (or FUSE subtypes) known to fake rename.
OBJECT_STORAGE = frozenset(
    {
        "fuse.s3fs",
        "fuse.gcsfuse",
        "fuse.blobfuse",
        "fuse.blobfuse2",
        "fuse.goofys",
        "fuse.mountpoint-s3",
        "fuse.rclone",
        "fuse.juicefs",
        "fuse.s3backer",
    }
)
NETWORK = frozenset({"nfs", "nfs4", "cifs", "smb3", "smbfs", "fuse.sshfs"})
# A host folder shared into a VM or container (Docker Desktop, WSL): probed, then local.
HOST_SHARE = frozenset({"9p", "virtiofs", "fuse.grpcfuse", "fakeowner"})

LOCAL, NETWORK_SHARE, UNSUPPORTED = "local", "network", "unsupported"


@dataclass(frozen=True)
class StorageCheck:
    kind: str  # local | network | unsupported
    fstype: str
    message: str | None = None

    @property
    def ok(self) -> bool:
        return self.kind != UNSUPPORTED


def _unescape(field: str) -> str:
    """mountinfo escapes space, tab, newline and backslash as octal."""
    out, i = [], 0
    while i < len(field):
        if field[i] == "\\" and i + 4 <= len(field) and field[i + 1 : i + 4].isdigit():
            out.append(chr(int(field[i + 1 : i + 4], 8)))
            i += 4
        else:
            out.append(field[i])
            i += 1
    return "".join(out)


def mount_type(path: Path, mountinfo: str) -> str:
    """The filesystem type of the longest mount point containing ``path``."""
    best, best_type = "", "unknown"
    # mountinfo is Linux's; compare in POSIX form so the tests also run on Windows.
    target = path.as_posix()
    for line in mountinfo.splitlines():
        left, sep, right = line.partition(" - ")
        if not sep:
            continue
        fields = left.split()
        if len(fields) < 5:
            continue
        mount_point = _unescape(fields[4])
        inside = target == mount_point or target.startswith(mount_point.rstrip("/") + "/")
        if inside and len(mount_point) >= len(best):
            best = mount_point
            best_type = right.split()[0] if right.split() else "unknown"
    return best_type


def rename_probe(folder: Path) -> bool:
    """Replace an existing file by rename and read it back."""
    tag = uuid.uuid4().hex
    target = folder / f".opncafyr-probe-{tag}"
    staged = folder / f".opncafyr-probe-{tag}.new"
    try:
        target.write_bytes(b"old")
        with staged.open("wb") as f:
            f.write(b"new")
            f.flush()
            os.fsync(f.fileno())
        os.replace(staged, target)
        return target.read_bytes() == b"new" and not staged.exists()
    except OSError:
        return False
    finally:
        for p in (staged, target):
            with contextlib.suppress(OSError):
                p.unlink()


def check_storage(folder: Path, mountinfo: str | None = None) -> StorageCheck:
    """Whether ``folder`` (an existing directory) can hold a study folder."""
    if mountinfo is None:
        try:
            mountinfo = MOUNTINFO.read_text("utf-8", errors="replace")
        except OSError:
            return StorageCheck(LOCAL, "unknown")
    fstype = mount_type(Path(os.path.realpath(folder)), mountinfo)
    if fstype in OBJECT_STORAGE:
        return StorageCheck(
            UNSUPPORTED,
            fstype,
            "This folder is on object storage mounted as a file system "
            f"({fstype}), which can't replace files safely. opncafyr would risk "
            "half-written manifests and runs there. Copy the study folder to a local "
            "disk or a network file share (NFS or SMB) and open it from there.",
        )
    if fstype in NETWORK or fstype in HOST_SHARE:
        if not rename_probe(Path(folder)):
            return StorageCheck(
                UNSUPPORTED,
                fstype,
                f"This folder is on a shared file system ({fstype}) that failed a "
                "safe-replace test. Use a local disk, or a share that supports rename.",
            )
        if fstype in HOST_SHARE:
            return StorageCheck(LOCAL, fstype)
        return StorageCheck(
            NETWORK_SHARE,
            fstype,
            f"This folder is on a network file share ({fstype}). Keep it open in one "
            "opncafyr at a time, and expect slower saves.",
        )
    return StorageCheck(LOCAL, fstype)


def owner_mismatch(folder: Path, container: bool = True) -> str | None:
    """A sentence if another user owns ``folder`` than the one running this process (#62).

    Git refuses such a folder ("dubious ownership"), and files this process writes there
    may not be readable by the person who mounted it. Checked only where POSIX owners
    exist; returns None on Windows, when the owner is the same, or when it can't be read.
    The message holds no path and no user id beyond the two numbers.
    """
    geteuid = getattr(os, "geteuid", None)
    if geteuid is None:
        return None
    try:
        owner = Path(folder).stat().st_uid
    except OSError:
        return None
    me = geteuid()
    if owner == me:
        return None
    first = (
        f"The study folder belongs to user {owner}, but opncafyr is running as user {me}. "
        "Git version history and file access may fail. "
    )
    if not container:
        return (
            first + "Run opncafyr as the user who owns the folder, or ask for the owner to match."
        )
    return first + (
        "Start the container with "
        '`opncafyr-container`, or pass `--user "$(id -u):$(id -g)"` (rootless Podman: '
        "`--userns=keep-id`), so the two match."
    )
