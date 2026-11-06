"""Entry point of an installer payload. install.sh / install.ps1 run it with the verified
bootstrap Python in isolated mode (``python -I -B run_installer.py <mode> [options]``).

It checks every file listed in ``payload.json`` against its SHA-256 *before* importing
anything from the wheel, then runs ``opncafyr.installer.cli`` straight from the wheel
(a zip on ``sys.path``): nothing is installed yet. Standard library only.
"""

import hashlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    try:
        manifest = json.loads((HERE / "payload.json").read_text("utf-8"))
        files = manifest["files"]
    except (OSError, ValueError, KeyError) as exc:
        print(f"error: {HERE} is not a complete installer ({exc})", file=sys.stderr)
        return 1
    for rel, sha in files.items():
        path = HERE / rel
        if not path.is_file() or _sha256(path) != sha:
            print(
                f"error: the installer is damaged ({rel} is missing or changed); download it again",
                file=sys.stderr,
            )
            return 1
    wheels = [HERE / rel for rel in files if rel.endswith(".whl")]
    if len(wheels) != 1:
        print("error: the installer must contain exactly one opncafyr wheel", file=sys.stderr)
        return 1
    sys.path.insert(0, str(wheels[0]))
    from opncafyr.installer.cli import main as cli_main

    args = sys.argv[1:] or ["install"]
    return cli_main(args, payload_dir=HERE)


if __name__ == "__main__":
    sys.exit(main())
