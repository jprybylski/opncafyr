"""Run by the new launcher environment before an install is committed.

    <launcher python> -I -m opncafyr.installer.selfcheck <version dir> <draft install.json>

The installer itself runs without jsonschema, so this is where its records are validated:
``runtime/runtime.json`` through ``core.runtime.load_runtime`` (schema ``runtime-v1``, every
executable an existing absolute path), the draft ``install.json`` against ``install-v1``.
Prints ``selfcheck ok`` and exits 0, or prints the problem and exits 1.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2:
        print("usage: python -m opncafyr.installer.selfcheck <version dir> <install.json>")
        return 2
    version_dir, draft = Path(args[0]), Path(args[1])
    try:
        from opncafyr.core.manifest import validate
        from opncafyr.core.runtime import load_runtime

        runtime = load_runtime(version_dir / "runtime" / "runtime.json")
        for name in ("python", "rscript", "deckifyr_python", "quarto", "uv"):
            runtime.executable(name)
        runtime.require_r_library()
        validate(json.loads(draft.read_text("utf-8")), "install-v1")
    except Exception as exc:  # noqa: BLE001 - reported to the installer, which stops
        print(f"selfcheck failed: {type(exc).__name__}: {exc}")
        return 1
    print("selfcheck ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
