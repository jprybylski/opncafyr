"""Schema-validated, atomically written JSON documents (manifests, workspace.json)."""

from __future__ import annotations

import json
import os
import tempfile
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


def validate(document: Any, schema_name: str) -> None:
    """Raise ``jsonschema.ValidationError`` if ``document`` does not match the schema."""
    from jsonschema import Draft202012Validator

    Draft202012Validator(load_schema(schema_name)).validate(document)


def atomic_write_json(path: str | Path, document: Any, schema_name: str | None = None) -> Path:
    """Validate (optionally) then write ``document`` so readers never see a partial file."""
    if schema_name is not None:
        validate(document, schema_name)
    return atomic_write_text(path, json.dumps(document, indent=2, sort_keys=True) + "\n")


def atomic_write_text(path: str | Path, text: str) -> Path:
    """Write ``text`` (UTF-8, ``\\n`` line ends) through a synced temp file and a rename."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return target
