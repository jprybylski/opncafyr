"""Schema-validated, atomically written JSON documents (manifests, workspace.json).

``read_document`` keeps each validated document in memory and reads the file again only
when its size, modification time or file ID changes (#67). The home page lists runs and
publications again and again, and on Windows every file open is far more expensive than
in a Linux container (endpoint security scans files as they are opened), so re-reading
unchanged manifests made each poll and view slower as a study grew. Every write here is a
rename (``atomic_write_bytes``), which gives the file a new ID and time, and an ordinary
edit changes the time, so a changed document is always read again. Integrity checks of a
run's files still hash them (``core.runs.verify_run``).
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import tempfile
import threading
from collections import OrderedDict
from functools import cache
from importlib import resources
from pathlib import Path
from typing import Any


@cache
def load_schema(name: str) -> dict[str, Any]:
    """Load a bundled JSON Schema by file stem, e.g. ``"run-manifest-v1"``."""
    text = resources.files("opncafyr.schemas").joinpath(f"{name}.schema.json").read_text("utf-8")
    schema = json.loads(text)
    # Imported here, not at module level: the installer (stdlib only) writes JSON with
    # atomic_write_json before jsonschema is installed.
    from jsonschema import Draft202012Validator

    Draft202012Validator.check_schema(schema)
    return schema


@cache
def _validator(schema_name: str) -> Any:
    """One validator per schema: building one resolves every ``$ref`` again."""
    from jsonschema import Draft202012Validator

    return Draft202012Validator(load_schema(schema_name))


def validate(document: Any, schema_name: str) -> None:
    """Raise ``jsonschema.ValidationError`` if ``document`` does not match the schema."""
    _validator(schema_name).validate(document)


# --- validated documents, read once while unchanged (#67) -------------------------------

_DOC_CACHE_MAX = 4096
_doc_cache: OrderedDict[tuple[str, str], tuple[tuple[int, ...], Any, str]] = OrderedDict()
_doc_lock = threading.Lock()


def file_signature(st: os.stat_result) -> tuple[int, ...]:
    """What changes whenever a file's content does: size, mtime, and (POSIX) ID and ctime.

    On Windows ``st_ctime`` is the creation time and ``os.scandir`` leaves ``st_ino`` at 0,
    so only size and mtime are used there.
    """
    if os.name == "nt":
        return (st.st_size, st.st_mtime_ns)
    return (st.st_size, st.st_mtime_ns, st.st_ino, st.st_ctime_ns)


def read_document(
    path: str | Path, schema_name: str, *, max_bytes: int | None = None
) -> tuple[Any, str]:
    """The validated JSON document at ``path`` and the SHA-256 of its bytes.

    Raises ``OSError`` (missing, unreadable), ``ValueError`` (not JSON, or larger than
    ``max_bytes``) or ``jsonschema.ValidationError``. While the file is unchanged, a call
    costs one ``stat`` and returns a copy of the document read before.
    """
    key = (os.fspath(path), schema_name)
    signature = file_signature(os.stat(path))
    with _doc_lock:
        hit = _doc_cache.get(key)
        if hit is not None and hit[0] == signature:
            _doc_cache.move_to_end(key)
            return copy.deepcopy(hit[1]), hit[2]
    with open(path, "rb") as fh:
        # The signature of what is read, so a change during the read is seen next time.
        signature = file_signature(os.fstat(fh.fileno()))
        if max_bytes is not None and signature[0] > max_bytes:
            raise ValueError("document too large")
        data = fh.read()
    document = json.loads(data.decode("utf-8"))
    validate(document, schema_name)
    digest = hashlib.sha256(data).hexdigest()
    with _doc_lock:
        _doc_cache[key] = (signature, document, digest)
        _doc_cache.move_to_end(key)
        while len(_doc_cache) > _DOC_CACHE_MAX:
            _doc_cache.popitem(last=False)
    return copy.deepcopy(document), digest


def clear_document_cache() -> None:
    with _doc_lock:
        _doc_cache.clear()


def schema_problems(document: Any, schema: dict[str, Any], label: str) -> list[str]:
    """Where ``document`` breaks ``schema``, as "<label> at /path fails 'rule'" lines.

    A problem names the place and the rule, never the value: jsonschema's own messages
    quote the offending value, and a job or log message must not carry data (ADR 0007).
    The draft is the one the schema's ``$schema`` names (2020-12 if it names none), so a
    tool's own schema, such as opnca-r's draft-07 ``artifacts.json`` schema, works too.
    """
    from jsonschema import Draft202012Validator
    from jsonschema.validators import validator_for

    cls = validator_for(schema, default=Draft202012Validator)
    return [
        f"{label} at /{'/'.join(map(str, e.absolute_path))} fails {e.validator!r}"
        for e in cls(schema).iter_errors(document)
    ]


def atomic_write_json(path: str | Path, document: Any, schema_name: str | None = None) -> Path:
    """Validate (optionally) then write ``document`` so readers never see a partial file."""
    if schema_name is not None:
        validate(document, schema_name)
    return atomic_write_text(path, json.dumps(document, indent=2, sort_keys=True) + "\n")


def atomic_write_text(path: str | Path, text: str) -> Path:
    """Write ``text`` (UTF-8, ``\\n`` line ends) through a synced temp file and a rename."""
    return atomic_write_bytes(path, text.encode("utf-8"))


def atomic_write_bytes(
    path: str | Path, data: bytes, *, mode: int | None = None, make_parents: bool = True
) -> Path:
    """Write ``data`` through a synced temp file in the same folder and a rename.

    Readers see the old file or the new one, never a partial write. ``mode`` sets the new
    file's permission bits (the temp file starts at 0600). ``make_parents=False`` refuses to
    create a missing folder.
    """
    target = Path(path)
    if make_parents:
        target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        if mode is not None:
            os.chmod(tmp, mode)
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return target
