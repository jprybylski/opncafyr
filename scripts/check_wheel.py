"""Check what a built opncafyr wheel contains (#41).

Development-only tools (the example_echo toy adapter) live in the source tree so the
registry finds them in a checkout, but ``pyproject.toml`` excludes them from the wheel.
This script fails if a wheel still carries one, or lacks a file a release needs. It runs
in ``scripts/make_installer.py`` and in CI.

Usage: ``uv run python scripts/check_wheel.py dist/opncafyr-*.whl``
"""

from __future__ import annotations

import sys
import zipfile
from collections.abc import Iterable
from pathlib import Path

# Folders a release wheel must not contain.
FORBIDDEN = ("opncafyr/adapters/example_echo/",)
# Files a release wheel must contain.
REQUIRED = (
    "opncafyr/adapters/opnca/descriptor.yaml",
    "opncafyr/adapters/deckifyr/descriptor.yaml",
    "opncafyr/adapters/quartifyr/descriptor.yaml",
    "opncafyr/web/static/index.html",
)


def check_wheel(
    path: Path, *, forbidden: Iterable[str] = FORBIDDEN, required: Iterable[str] = REQUIRED
) -> list[str]:
    """Problems with the wheel at ``path``; empty when it is fit to ship."""
    with zipfile.ZipFile(path) as wheel:
        names = set(wheel.namelist())
    problems = [
        f"{name} must not be in a release wheel"
        for prefix in forbidden
        for name in sorted(names)
        if name.startswith(prefix)
    ][:20]
    problems += [f"{name} is missing" for name in required if name not in names]
    return problems


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__.strip().splitlines()[-1], file=sys.stderr)
        return 2
    failed = False
    for arg in argv:
        problems = check_wheel(Path(arg))
        for problem in problems:
            print(f"{arg}: {problem}", file=sys.stderr)
        failed |= bool(problems)
        if not problems:
            print(f"{arg}: ok")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
