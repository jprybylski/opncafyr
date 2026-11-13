"""A stand-in for ``python -I -m deckifyr --json <command>`` (#33 unit tests).

Called as ``fake_deckifyr.py <fake root> -I -m deckifyr --json <command> ...`` by the
wrapper ``fake_deck_runtime`` writes. ``validate.txt`` and ``build.txt`` in the root pick
the behaviour (ok, warn, fail, usage, garbage, badsha, nomanifest). Each build writes
``seen.json``: the files the staged ``OUTPUTS/`` held and the argv.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

import yaml


def ok(**fields) -> int:
    print(json.dumps({"status": "ok", "result_version": 1, **fields}))
    return 0


def error(code: str, message: str) -> int:
    print(
        json.dumps({"status": "error", "result_version": 1, "code": code, "message": message}),
        file=sys.stderr,
    )
    return 1


def mode(root: Path, name: str) -> str:
    path = root / f"{name}.txt"
    return path.read_text("utf-8").strip() if path.exists() else "ok"


def init(root: Path, folder: Path, rest: list[str]) -> int:
    folder.mkdir(parents=True, exist_ok=True)
    (root / "init-args.json").write_text(json.dumps(rest), "utf-8")
    if "--from-dir" in rest:
        # Like deckifyr: copy the design and layouts the template's presentation names.
        import shutil

        source = Path(rest[rest.index("--from-dir") + 1])
        if "--type" in rest:
            source = source / "templates" / rest[rest.index("--type") + 1]
        doc = yaml.safe_load((source / "presentation.yaml").read_text("utf-8"))
        names = [doc["design"]["base"], doc["layouts"]]
        for name in names:
            shutil.copyfile(source / name, folder / name)
        (folder / "presentation.yaml").write_text("deckifyr: '0.1'\nslides: []\n", "utf-8")
        return ok(directory=str(folder), created=[*names, "presentation.yaml"])
    (folder / "design.yaml").write_text("deckifyr: '0.1'\n", "utf-8")
    (folder / "layouts.yaml").write_text("deckifyr: '0.1'\nlayouts: {blank: {}}\n", "utf-8")
    (folder / "presentation.yaml").write_text("deckifyr: '0.1'\nslides: []\n", "utf-8")
    return ok(directory=str(folder), created=["design.yaml", "layouts.yaml", "presentation.yaml"])


def validate(root: Path, presentation: Path) -> int:
    m = mode(root, "validate")
    if m == "fail":
        return error(
            "E_CONTENT_VALIDATION", f"artifact not found: {presentation.parent}/OUTPUTS/x.png"
        )
    if m == "usage":
        print("usage: deckifyr validate [-h]", file=sys.stderr)
        return 2
    warnings = []
    if m == "warn":
        warnings = [
            {
                "code": "W_UNITLESS_LENGTH",
                "message": f"In {presentation}: 3 has no unit",
                "slide": "s01-table",
                "element": "content",
            }
        ]
    return ok(valid=True, presentation=str(presentation), slide_count=1, warnings=warnings)


def build(root: Path, presentation: Path, argv: list[str]) -> int:
    project = presentation.parent
    seen = sorted(
        p.relative_to(project).as_posix() for p in (project / "OUTPUTS").rglob("*") if p.is_file()
    )
    (root / "seen.json").write_text(
        json.dumps({"files": seen, "argv": argv, "cwd": os.getcwd()}), "utf-8"
    )
    m = mode(root, "build")
    if m == "fail":
        return error("E_UNTRUSTED_BINARY", f"build.quarto.binary names /usr/bin/evil in {project}")
    if m == "garbage":
        return 0
    doc = yaml.safe_load(presentation.read_text("utf-8"))
    settings = doc["build"]
    pptx = project / settings["output"]
    pptx.parent.mkdir(parents=True, exist_ok=True)
    pptx.write_bytes(b"PK fake pptx")
    sha = hashlib.sha256(pptx.read_bytes()).hexdigest()
    previews = []
    pdir = pptx.parent / "previews"
    pdir.mkdir(exist_ok=True)
    (pdir / "stale-01.png").write_bytes(b"old")  # never listed: must not be taken
    if settings.get("previews"):
        for n in (1, 2):
            image = pdir / f"nca-results-{n:02d}.png"
            image.write_bytes(f"preview {n}".encode())
            previews.append(str(image))
    manifest = {
        "deckifyr_version": "0.3.6",
        "output": {"path": str(pptx), "sha256": sha},
        "previews": previews,
        "preview_pdf": None,
        "warnings": [],
    }
    if m == "badsha":
        manifest["output"]["sha256"] = "0" * 64
    manifest_path = project / settings["manifest"]
    if m != "nomanifest":
        manifest_path.write_text(json.dumps(manifest), "utf-8")
    return ok(
        output=str(pptx),
        manifest=None if m == "nomanifest" else str(manifest_path),
        slide_count=1,
        warning_count=1,
        previews=previews,
        preview_pdf=None,
        warnings=[
            {
                "code": "W_GENERAL",
                "message": f"note about {project}/x",
                "slide": None,
                "element": None,
            }
        ],
        artifacts=[{"kind": "pptx", "path": str(pptx), "sha256": sha}],
    )


def main() -> int:
    root, argv = Path(sys.argv[1]), sys.argv[2:]
    assert argv[:5] == ["-I", "-m", "deckifyr", "--json", argv[4]], argv
    command, rest = argv[4], argv[5:]
    if command == "init":
        return init(root, Path(rest[0]), rest[1:])
    if command == "validate":
        return validate(root, Path(rest[0]))
    if command == "build":
        return build(root, Path(rest[0]), argv)
    return error("E_NOT_IMPLEMENTED", command)


if __name__ == "__main__":
    sys.exit(main())
