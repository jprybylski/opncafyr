"""Summarize a CSV's columns for the example_echo toy job (#41). Standard library only.

Usage: ``python -I summarize.py <source.csv> <output folder>``. Writes
``<output folder>/summary.json``: the row count and, per column, its name and how many
rows are non-empty. It never writes a cell value, so a summary can't leak study data.
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

PROGRESS = "OPNCAFYR-PROGRESS: "


def summarize(source: Path) -> dict:
    with source.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.reader(fh)
        header = next(reader, [])
        counts = [0] * len(header)
        rows = 0
        for row in reader:
            rows += 1
            for i, cell in enumerate(row[: len(header)]):
                if cell.strip():
                    counts[i] += 1
    return {
        "schema_version": "1.0",
        "rows": rows,
        "columns": [{"name": n, "non_empty": c} for n, c in zip(header, counts, strict=True)],
    }


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: summarize.py <source.csv> <output folder>", file=sys.stderr)
        return 2
    source, out = Path(argv[0]), Path(argv[1])
    print(PROGRESS + "Reading the CSV", flush=True)
    try:
        doc = summarize(source)
    except (OSError, UnicodeDecodeError, csv.Error) as exc:
        # The exception type only: its text could quote a line of the file.
        print(f"summarize: cannot read the CSV ({type(exc).__name__})", file=sys.stderr)
        return 3
    (out / "summary.json").write_text(json.dumps(doc, indent=2) + "\n", "utf-8")
    print(PROGRESS + f"Summarized {len(doc['columns'])} columns", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
