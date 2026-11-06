"""Smoke tests run against a staged install before it is activated (and again by repair).

Each returns a result ``{name, status, seconds, detail}``; any failure stops the install
and the previous version stays active. They prove that the parts start and agree with the
pins, not that any analysis is right (research use only).
"""

from __future__ import annotations

import json
import shutil
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from opncafyr.installer import proc

Result = dict[str, Any]


class SmokeFailed(RuntimeError):
    def __init__(self, results: list[Result]) -> None:
        failed = [r for r in results if r["status"] != "ok"]
        super().__init__(
            "smoke tests failed: " + "; ".join(f"{r['name']}: {r['detail']}" for r in failed)
        )
        self.results = results


def _check(
    name: str, fn: Callable[[], str], results: list[Result], log: Path | None = None
) -> None:
    started = time.monotonic()
    try:
        detail, status = fn(), "ok"
    except Exception as exc:  # noqa: BLE001 - every failure becomes a reported result
        detail, status = str(exc)[-800:], "failed"
    results.append(
        {
            "name": name,
            "status": status,
            "seconds": round(time.monotonic() - started, 1),
            "detail": detail,
        }
    )
    if log is not None:
        with log.open("a", encoding="utf-8") as fh:
            fh.write(f"[{status}] {name}: {detail}\n")


def _out(argv: list[Any], env: Mapping[str, str], cwd: Path | None = None) -> str:
    done = proc.capture(argv, env=env, cwd=cwd, timeout=600)
    out = done.stdout.decode(errors="replace").strip()
    if done.returncode != 0:
        err = done.stderr.decode(errors="replace").strip()
        raise RuntimeError(f"exit {done.returncode}: {(err or out)[-600:]}")
    return out


def run_all(
    runtime: Mapping[str, Any],
    *,
    versions: Mapping[str, str],
    env: Mapping[str, str],
    work: Path,
    log: Path | None = None,
) -> list[Result]:
    """``runtime`` is a runtime-v1 document; ``versions`` the pinned versions to expect."""
    exe = {k: Path(v) for k, v in runtime["executables"].items()}
    lib = runtime["r_library"]
    r_env = {**env, "R_LIBS_USER": lib, "R_LIBS": lib}
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    results: list[Result] = []

    def launcher() -> str:
        code = "import opncafyr, fastapi, uvicorn, jsonschema, psutil, opncafyr.cli; "
        code += "print(opncafyr.__version__)"
        got = _out([exe["python"], "-I", "-c", code], env)
        if got != versions["opncafyr"]:
            raise RuntimeError(f"launcher is {got}, expected {versions['opncafyr']}")
        return f"opncafyr {got}"

    def tools() -> str:
        code = "import deckifyr, docx, yaml, PIL, pymupdf, fastapi; print(deckifyr.__version__)"
        got = _out([exe["deckifyr_python"], "-I", "-c", code], env)
        if got != versions["deckifyr"]:
            raise RuntimeError(f"deckifyr is {got}, expected {versions['deckifyr']}")
        return f"deckifyr {got}"

    def r_packages() -> str:
        code = (
            "suppressPackageStartupMessages({library(opnca); library(quartifyr); "
            "library(reportifyr)}); cat(as.character(packageVersion('opnca')))"
        )
        got = _out([exe["rscript"], "--vanilla", "-e", code], r_env).splitlines()[-1]
        if got != versions["opnca-r"]:
            raise RuntimeError(f"opnca is {got}, expected {versions['opnca-r']}")
        return f"library(opnca) {got}, quartifyr, reportifyr"

    def quarto() -> str:
        got = _out([exe["quarto"], "--version"], env).splitlines()[-1].strip()
        if got != versions["quarto"]:
            raise RuntimeError(f"quarto is {got}, expected {versions['quarto']}")
        return f"quarto {got}"

    def deckifyr_validate() -> str:
        deck = work / "deck"
        py = exe["deckifyr_python"]
        _out([py, "-I", "-m", "deckifyr", "--json", "init", deck], env, cwd=work)
        out = _out(
            [py, "-I", "-m", "deckifyr", "--json", "validate", deck / "presentation.yaml"],
            env,
            cwd=work,
        )
        doc = json.loads(out)
        if doc.get("status") != "ok":
            raise RuntimeError(f"validate status {doc.get('status')}: {out[-400:]}")
        return "deckifyr validate ok"

    for name, fn in (
        ("launcher_import", launcher),
        ("tools_import", tools),
        ("r_packages", r_packages),
        ("quarto_version", quarto),
        ("deckifyr_validate", deckifyr_validate),
    ):
        _check(name, fn, results, log)
    shutil.rmtree(work, ignore_errors=True)
    return results
