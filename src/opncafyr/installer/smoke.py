"""Smoke tests run against a staged install before it is activated (and again by repair).

Each returns a result ``{name, status, seconds, detail}``; any failure stops the install
and the previous version stays active. They prove that the parts start and agree with the
pins, not that any analysis is right (research use only). The last three are the
installation report's checks (#36): an opnca-r calculation against known values on
synthetic data, a Quarto DOCX render with an R chunk run by the pinned R, and a deckifyr
build with only the pinned binaries.
"""

from __future__ import annotations

import json
import shutil
import sys
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


def _resource(name: str) -> str:
    from importlib import resources

    return resources.files("opncafyr.installer").joinpath(name).read_text("utf-8")


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
    r_env = {**env, "R_LIBS_USER": lib, "R_LIBS": lib, "R_LIBS_SITE": ""}
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

    def r_isolation() -> str:
        """R searches the app's library and its own only, never the user's (#34)."""
        code = (
            "cat(normalizePath(.libPaths(), winslash = '/'), sep = '\\n'); cat('\\n--\\n'); "
            "cat(normalizePath(R.home(), winslash = '/'))"
        )
        out = _out([exe["rscript"], "--vanilla", "-e", code], r_env)
        return check_lib_paths(out, lib)

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

    def opnca_calc() -> str:
        script = work / "smoke_opnca.R"
        script.write_text(_resource("r/smoke_opnca.R"), "utf-8")
        return _out([exe["rscript"], "--vanilla", script], r_env, cwd=work).splitlines()[-1]

    def quarto_docx() -> str:
        """Quarto renders a document with one R chunk (run by the pinned R) to DOCX."""
        doc = work / "report"
        doc.mkdir()
        (doc / "smoke.qmd").write_text(
            "---\ntitle: opncafyr installation check\nformat: docx\n---\n\n"
            "Synthetic numbers only.\n\n```{r}\nx <- c(1, 2, 3)\nmean(x)\n```\n",
            "utf-8",
        )
        qenv = {**r_env, "QUARTO_R": str(exe["rscript"].parent)}
        _out([exe["quarto"], "render", "smoke.qmd", "--to", "docx"], qenv, cwd=doc)
        out = doc / "smoke.docx"
        if not out.is_file() or out.read_bytes()[:2] != b"PK":
            raise RuntimeError("no DOCX was written")
        return f"DOCX {out.stat().st_size} bytes"

    def deckifyr_build() -> str:
        deck = work / "deck"
        py = exe["deckifyr_python"]
        out = _out(
            [py, "-I", "-m", "deckifyr", "--json", "build", deck / "presentation.yaml"]
            + ["--trusted-binaries-only", "--rscript-bin", exe["rscript"]]
            + ["--quarto-bin", exe["quarto"]],
            env,
            cwd=work,
        )
        doc = json.loads(out)
        pptx = [a for a in doc.get("artifacts", []) if a.get("kind") == "pptx"]
        if doc.get("status") != "ok" or not pptx:
            raise RuntimeError(f"build status {doc.get('status')}: {out[-400:]}")
        path = Path(pptx[0]["path"])
        if path.read_bytes()[:2] != b"PK":
            raise RuntimeError(f"{path.name} is not a PPTX")
        return f"PPTX {path.stat().st_size} bytes"

    for name, fn in (
        ("launcher_import", launcher),
        ("tools_import", tools),
        ("r_packages", r_packages),
        ("r_library_isolation", r_isolation),
        ("quarto_version", quarto),
        ("deckifyr_validate", deckifyr_validate),
        ("opnca_calculation", opnca_calc),
        ("quarto_docx_render", quarto_docx),
        ("deckifyr_build", deckifyr_build),
    ):
        _check(name, fn, results, log)
    shutil.rmtree(work, ignore_errors=True)
    return results


def check_lib_paths(output: str, lib: str) -> str:
    """Check ``.libPaths()`` (one per line, then ``--``, then ``R.home()``).

    Every library must be the app's library or inside R's own installation; anything else
    (the user's ``~/Library/R`` or ``%LOCALAPPDATA%\\R`` library, a site library) fails.
    """
    paths_text, sep, home = output.replace("\r\n", "\n").rpartition("\n--\n")
    if not sep or not home.strip():
        raise RuntimeError(f"unexpected .libPaths() output: {output[-300:]!r}")

    def norm(p: str) -> str:
        p = p.strip().replace("\\", "/").rstrip("/")
        return p.lower() if sys.platform == "win32" else p

    app, r_home = norm(str(Path(lib).resolve())), norm(home)
    paths = [norm(p) for p in paths_text.splitlines() if p.strip()]
    foreign = [p for p in paths if p != app and not (p + "/").startswith(r_home + "/")]
    if foreign or app not in paths:
        raise RuntimeError(
            f"R library paths outside the app: {foreign or paths} (app library {app})"
        )
    return f"{len(paths)} library paths: the app's and R's own"
