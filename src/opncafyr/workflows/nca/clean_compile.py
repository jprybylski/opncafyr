"""Compile a recipe into a standalone, human-readable R script (#22).

The script is the fixed library ``opncafyr/r/nca_clean_lib.R`` followed by one call per
step. Recipe text reaches R only as string literals made by ``r_string``: printable ASCII
stays as it is, ``"`` and ``\\`` are escaped and everything else becomes a ``\\u{...}``
escape, so no column name, value or note can end a string or start code. Names in the
generated code are fixed identifiers; user-supplied keys (recode and flag tables) become
two parallel vectors.

Paths and the preview sample come from the environment, with defaults that let a person
run the script by hand from the study folder::

    Rscript .opncafyr/cleaned/<id>/clean.R
"""

from __future__ import annotations

import csv
import io
from importlib import resources
from pathlib import Path
from typing import Any

from opncafyr.workflows.nca.recipe import recipe_hash

ENV_INPUT = "CLEAN_INPUT"
ENV_OUTPUT = "CLEAN_OUTPUT"
ENV_SAMPLE = "CLEAN_SAMPLE_ROWS"


def library_source() -> str:
    return resources.files("opncafyr.r").joinpath("nca_clean_lib.R").read_text("utf-8")


def r_string(text: str) -> str:
    """An R string literal for ``text`` that can only ever be data."""
    out = ['"']
    for ch in text:
        code = ord(ch)
        if ch == '"':
            out.append('\\"')
        elif ch == "\\":
            out.append("\\\\")
        elif 0x20 <= code < 0x7F:
            out.append(ch)
        elif code == 0:
            raise ValueError("Text with a NUL character can't be used in a recipe.")
        elif code <= 0xFFFF:
            out.append(f"\\u{{{code:04x}}}")
        else:
            out.append(f"\\U{{{code:x}}}")
    out.append('"')
    return "".join(out)


def r_strings(values: list[str]) -> str:
    if not values:
        return "character(0)"
    return "c(" + ", ".join(r_string(v) for v in values) + ")"


def r_logicals(values: list[bool]) -> str:
    return "c(" + ", ".join("TRUE" if v else "FALSE" for v in values) + ")"


def _where(w: dict[str, Any]) -> str:
    if "all" in w:
        return "list(all = list(" + ", ".join(_where(x) for x in w["all"]) + "))"
    col = r_string(w["column"])
    if "equals" in w:
        return f"list(column = {col}, equals = {r_string(w['equals'])})"
    if "in" in w:
        return f"list(column = {col}, values = {r_strings(list(w['in']))})"
    return f"list(column = {col}, is_blank = TRUE)"


def _args(step: dict[str, Any]) -> str:
    op, a = step["op"], step["args"]
    s = r_string
    if op == "clean_names":
        return "list()"
    if op == "rename":
        return f"list(from = {s(a['from'])}, to = {s(a['to'])})"
    if op in ("trim", "report_dupes"):
        return f"list(columns = {r_strings(list(a['columns']))})"
    if op == "parse_type":
        return (
            f"list(column = {s(a['column'])}, type = {s(a['type'])}, "
            f"decimal_mark = {s(a['decimal_mark'])})"
        )
    if op == "recode":
        keys = list(a["map"].keys())
        return (
            f"list(column = {s(a['column'])},\n         from = {r_strings(keys)},\n"
            f"         to = {r_strings([a['map'][k] for k in keys])})"
        )
    if op == "derive":
        if a["fn"] == "copy":
            return f'list(target = {s(a["target"])}, fn = "copy", from = {s(a["from"])})'
        if a["fn"] == "concat":
            return (
                f'list(target = {s(a["target"])}, fn = "concat", '
                f"from = {r_strings(list(a['from']))}, sep = {s(a['sep'])})"
            )
        return f'list(target = {s(a["target"])}, fn = "constant", value = {s(a["value"])})'
    if op == "derive_elapsed_time":
        return (
            f"list(target = {s(a['target'])}, from = {s(a['from'])}, "
            f"reference = {s(a['reference'])},\n         unit = {s(a['unit'])}, "
            f"time_zone = {s(a['time_zone'])})"
        )
    if op == "convert_units":
        return (
            f"list(value_column = {s(a['value_column'])}, unit_column = {s(a['unit_column'])}, "
            f"from = {s(a['from'])}, to = {s(a['to'])})"
        )
    if op == "flag":
        keys = list(a["table"].keys())
        return (
            f"list(target = {s(a['target'])}, column = {s(a['column'])},\n"
            f"         values = {r_strings(keys)},\n"
            f"         flags = {r_logicals([a['table'][k] for k in keys])})"
        )
    if op in ("exclude", "filter_rows"):
        return f"list(where = {_where(a['where'])},\n         reason = {s(a['reason'])})"
    raise ValueError(f"Unknown op {op!r}")


def _describe(step: dict[str, Any]) -> str:
    """A fixed-vocabulary comment for a step (no recipe text, so it can't break out)."""
    return {
        "clean_names": "clean column names",
        "rename": "rename a column",
        "trim": "trim spaces",
        "parse_type": "check numbers (and convert decimal commas)",
        "recode": "replace listed values",
        "derive": "add a derived column",
        "derive_elapsed_time": "derive elapsed time from two date-times",
        "convert_units": "convert units with a fixed factor",
        "flag": "add a TRUE/FALSE column from a value table",
        "exclude": "mark rows excluded, with a reason (rows stay)",
        "filter_rows": "move rows to the rejected-row log, with a reason",
        "report_dupes": "report duplicate rows (nothing removed)",
    }[step["op"]]


def compile_recipe(doc: dict[str, Any], *, source_rel: str, delimiter: str) -> str:
    """The full ``clean.R`` text for ``doc`` reading ``source_rel`` (workspace-relative)."""
    lines = [
        "# Cleaning script generated by opncafyr from a recipe (#22).",
        f"# recipe sha256: {recipe_hash(doc)}",
        "# Run it from the study folder: Rscript <path to this file>",
        "# Inputs and outputs: CLEAN_INPUT, CLEAN_OUTPUT, CLEAN_SAMPLE_ROWS (0 = all rows).",
        "# The source file is only read. Rows leave the data only through filter_rows,",
        "# into rejected.csv with the step and reason.",
        "",
        library_source().rstrip("\n"),
        "",
        "# --- This recipe ---------------------------------------------------------------",
        "",
        '.script <- sub("^--file=", "", grep("^--file=", commandArgs(FALSE), value = TRUE))',
        '.here <- if (length(.script) == 1L) dirname(.script) else "."',
        f"input <- Sys.getenv({r_string(ENV_INPUT)}, {r_string(source_rel)})",
        f'output <- Sys.getenv({r_string(ENV_OUTPUT)}, file.path(.here, "rerun"))',
        f'sample_rows <- as.integer(Sys.getenv({r_string(ENV_SAMPLE)}, "0"))',
        "",
        "steps <- list(",
    ]
    calls = []
    for i, step in enumerate(doc["steps"], start=1):
        note = f", note = {r_string(step['note'])}" if step.get("note") else ""
        calls.append(
            f"  # {i}. {_describe(step)}\n"
            f"  list(op = {r_string(step['op'])}{note},\n"
            f"       args = {_args(step)})"
        )
    lines.append(",\n".join(calls))
    lines += [
        ")",
        "",
        f"oc_run(input, output, sep = {r_string(delimiter)}, sample_rows = sample_rows,",
        f"       recipe_id = {r_string(doc['id'])}, recipe_sha256 = {r_string(recipe_hash(doc))},",
        "       steps = steps)",
        "",
    ]
    return "\n".join(lines)


def read_header(path: Path, delimiter: str) -> list[str]:
    """The header row as the R reader sees it (BOM removed, quotes honoured)."""
    with path.open("rb") as fh:
        first = fh.readline(1 << 20)
    text = first.decode("utf-8", errors="replace").lstrip("﻿").rstrip("\r\n")
    return next(csv.reader(io.StringIO(text), delimiter=delimiter), [])
