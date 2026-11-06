"""CSV importer: delimiter sniffing, UTF-8 BOM, missing tokens and readr-like types.

Ported from makecoolplots ``crates/mcp-inspect`` (Rust):
- delimiter sniffing over the first 64 KiB and 50 records among ``, \\t ; |``, scored by
  row-width consistency, then header width (``src/read.rs`` ``sniff_delimiter``);
- a UTF-8 byte-order mark is stripped and reported (``src/read.rs``);
- missing tokens ``"", NA, ., NaN, NULL``, compared after trimming (``src/lib.rs``);
- type inference integer -> double -> logical -> date -> datetime -> character. A
  character column that a narrower type fits for at least half its values reports that
  type and up to three values that don't fit (``src/infer.rs``).

The file is opened read-only and never locked: Python's ``open`` on Windows uses the
CRT's share-deny-none mode. A file up to the size cap is read once, so the SHA-256 and
the parsed rows describe the same bytes. A larger file is hashed by streaming, and only
its first rows are parsed; the row count is then an estimate and says so.
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
import sys
from collections import Counter
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import date
from functools import lru_cache
from pathlib import Path

from opncafyr.core.datasets import (
    MAX_MISFITS,
    VALUE_CAP,
    ColumnProfile,
    DatasetPreview,
    ValueCount,
    fingerprint,
)

CANDIDATE_DELIMITERS = (",", "\t", ";", "|")
SNIFF_CHARS = 64 * 1024
SNIFF_RECORDS = 50
MISSING_TOKENS = ("", "NA", ".", "NaN", "NULL")
_MISSING = frozenset(MISSING_TOKENS)
UTF8_BOM = b"\xef\xbb\xbf"
INT_MAX = 2_147_483_647  # R's integer range; the minimum is NA
MAX_LINE_EXAMPLES = 5
DELIMITER_NAMES = {",": "comma", "\t": "tab", ";": "semicolon", "|": "pipe"}

# A large field must not stop the preview (csv's default limit is 128 KiB).
csv.field_size_limit(min(sys.maxsize, 2**31 - 1))

_DOUBLE = re.compile(r"[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?")
_LOGICAL = frozenset({"TRUE", "FALSE", "T", "F", "true", "false", "True", "False"})
_ISO = re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})")
_SLASH = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})")
_TIME = re.compile(r"([T ])(\d{1,2}):(\d{2})(?::(\d{2})(\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?")
DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y")


def _date_prefix(fmt: int, s: str) -> tuple[date, str] | None:
    m = (_ISO if fmt == 0 else _SLASH).match(s)
    if m is None:
        return None
    a, b, c = (int(g) for g in m.groups())
    y, mo, d = (a, b, c) if fmt == 0 else (c, b, a) if fmt == 1 else (c, a, b)
    try:
        return date(y, mo, d), s[m.end() :]
    except ValueError:
        return None


def _time_layout(rest: str) -> str | None:
    """The strptime layout of a datetime's time part, or None if it isn't one."""
    m = _TIME.fullmatch(rest)
    if m is None:
        return None
    sep, hh, mm, ss, frac, tz = m.groups()
    if int(hh) > 23 or int(mm) > 59 or (ss is not None and int(ss) > 59):
        return None
    layout = f"{sep}%H:%M"
    if ss is not None:
        layout += ":%S"
    if frac:
        layout += ".%f"
    if tz:
        layout += "Z" if tz == "Z" else "%z"
    return layout


@dataclass(frozen=True)
class _Kinds:
    integer: bool
    number: float | None
    logical: bool
    # Per date format: the date (plain date) and the datetime's (date, layout).
    dates: tuple[date | None, ...]
    datetimes: tuple[tuple[date, str] | None, ...]


@lru_cache(maxsize=65536)
def _kinds(v: str) -> _Kinds:
    number: float | None = None
    body = v[1:] if v[:1] in "+-" else v
    if body == "Inf" or _DOUBLE.fullmatch(v):
        number = float(v)
    integer = number is not None and body.isascii() and body.isdigit() and abs(int(v)) <= INT_MAX
    dates: list[date | None] = [None, None, None]
    datetimes: list[tuple[date, str] | None] = [None, None, None]
    if v[:1].isdigit() and ("-" in v or "/" in v):
        for i in range(3):
            parsed = _date_prefix(i, v)
            if parsed is None:
                continue
            d, rest = parsed
            if not rest:
                dates[i] = d
            elif (layout := _time_layout(rest)) is not None:
                datetimes[i] = (d, layout)
    return _Kinds(integer, number, v in _LOGICAL, tuple(dates), tuple(datetimes))


@dataclass
class _Candidate:
    ok: int = 0
    misfits: list[str] = field(default_factory=list)

    def record(self, accepted: bool, value: str) -> None:
        if accepted:
            self.ok += 1
        elif len(self.misfits) < MAX_MISFITS and value not in self.misfits:
            self.misfits.append(value)


@dataclass
class _Tally:
    """Running type evidence and raw value counts for one column."""

    n: int = 0
    missing: int = 0
    integer: _Candidate = field(default_factory=_Candidate)
    double: _Candidate = field(default_factory=_Candidate)
    logical: _Candidate = field(default_factory=_Candidate)
    date: list[_Candidate] = field(default_factory=lambda: [_Candidate() for _ in range(3)])
    datetime: list[_Candidate] = field(default_factory=lambda: [_Candidate() for _ in range(3)])
    low: float | None = None
    high: float | None = None
    date_range: list[tuple[date, date] | None] = field(default_factory=lambda: [None] * 3)
    layouts: list[Counter[str]] = field(default_factory=lambda: [Counter() for _ in range(3)])
    values: Counter[str] | None = field(default_factory=Counter)

    def push(self, raw: str) -> None:
        values = self.values
        if values is not None:
            if raw in values or len(values) < VALUE_CAP:
                values[raw] += 1
            else:
                self.values = None
        v = raw.strip()
        if v in _MISSING:
            self.missing += 1
            return
        self.n += 1
        k = _kinds(v)
        self.integer.record(k.integer, v)
        self.double.record(k.number is not None, v)
        if k.number is not None and k.number not in (float("inf"), float("-inf")):
            self.low = k.number if self.low is None else min(self.low, k.number)
            self.high = k.number if self.high is None else max(self.high, k.number)
        self.logical.record(k.logical, v)
        for i in range(3):
            d = k.dates[i]
            self.date[i].record(d is not None, v)
            if d is not None:
                r = self.date_range[i]
                self.date_range[i] = (d, d) if r is None else (min(r[0], d), max(r[1], d))
            dt = k.datetimes[i]
            self.datetime[i].record(dt is not None, v)
            if dt is not None:
                self.layouts[i][dt[1]] += 1

    def profile(self, name: str) -> ColumnProfile:
        p = ColumnProfile(name=name, type="character", n_values=self.n, n_missing=self.missing)
        if self.values is None:
            p.values_capped = True
        else:
            ranked = sorted(self.values.items(), key=lambda kv: (-kv[1], kv[0]))
            p.values = [ValueCount(v, c) for v, c in ranked]
        n = self.n
        if n == 0:
            p.type = "logical"
            p.notes.append("Every value is missing (readr reads such a column as logical).")
            return p
        if self.integer.ok == n or self.double.ok == n:
            p.type = "integer" if self.integer.ok == n else "double"
            p.minimum, p.maximum = self.low, self.high
            return p
        if self.logical.ok == n:
            p.type = "logical"
            return p
        for kind, cands in (("date", self.date), ("datetime", self.datetime)):
            fits = [i for i in range(3) if cands[i].ok == n]
            if not fits:
                continue
            i = fits[0]
            p.type = kind
            p.format = DATE_FORMATS[i]
            if len(fits) > 1:
                p.notes.append(
                    "Day and month can't be told apart in every value; read as "
                    f"{DATE_FORMATS[i]}. Check the date format."
                )
            if kind == "date":
                r = self.date_range[i]
                if r is not None:
                    p.minimum, p.maximum = r[0].isoformat(), r[1].isoformat()
            else:
                layouts = self.layouts[i]
                common = sorted(layouts.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
                p.format += common
                if len(layouts) > 1:
                    p.notes.append(
                        "Times are written in more than one way (for example with and "
                        f"without seconds); the most common is {p.format}."
                    )
            return p
        # Character. Report a narrower type that fits at least half the values.
        best: tuple[_Candidate, str] | None = None
        options = [(self.double, "number"), (self.logical, "logical")]
        options += [(c, "date") for c in self.date] + [(c, "datetime") for c in self.datetime]
        for cand, label in options:
            if best is None or cand.ok > best[0].ok:
                best = (cand, label)
        if best is not None and best[0].ok > 0 and best[0].ok * 2 >= n:
            cand, label = best
            p.near_type = label
            p.misfits = list(cand.misfits)
            plural = {"number": "numbers", "logical": "logical values"}.get(label, f"{label}s")
            rest = n - cand.ok
            p.notes.append(
                f"Read as text: {cand.ok} of {n} values are {plural}, but {rest} "
                f"{'is' if rest == 1 else 'are'} not."
            )
        return p


def sniff_delimiter(text: str, complete: bool) -> str:
    """The delimiter whose rows have the most consistent width, then the widest header."""
    if not complete and "\n" in text:
        text = text[: text.rfind("\n") + 1]  # drop a line cut off by the sniff window
    best: tuple[tuple[int, int], str] | None = None
    for d in CANDIDATE_DELIMITERS:
        counts: list[int] = []
        reader = csv.reader(io.StringIO(text, newline=""), delimiter=d)
        try:
            for record in reader:
                if record:
                    counts.append(len(record))
                if len(counts) >= SNIFF_RECORDS:
                    break
        except csv.Error:
            pass
        if not counts or counts[0] < 2:
            continue
        header, rest = counts[0], counts[1:]
        consistent = 1000 if not rest else sum(n == header for n in rest) * 1000 // len(rest)
        score = (consistent, header)
        if best is None or score > best[0]:
            best = (score, d)
    return best[1] if best else ","


def _decode(raw: bytes) -> tuple[str, bool]:
    try:
        return raw.decode("utf-8"), True
    except UnicodeDecodeError:
        return raw.decode("utf-8", errors="replace"), False


def _lines(numbers: list[int], total: int) -> str:
    shown = ", ".join(str(n) for n in numbers)
    return shown + (f" and {total - len(numbers)} more" if total > len(numbers) else "")


class CsvImporter:
    id = "csv"
    label = "CSV"
    extensions = (".csv",)

    def read(
        self,
        path: Path,
        *,
        head_rows: int,
        full_scan_max_bytes: int,
        sample_rows: int,
    ) -> DatasetPreview:
        size = path.stat().st_size
        if size <= full_scan_max_bytes:
            with path.open("rb") as fh:
                data = fh.read()
            sha256 = hashlib.sha256(data).hexdigest()
            size = len(data)
            bom = data.startswith(UTF8_BOM)
            text, utf8 = _decode(data[3:] if bom else data)
            delimiter = sniff_delimiter(text[:SNIFF_CHARS], len(text) <= SNIFF_CHARS)
            parsed = _parse(io.StringIO(text, newline=""), delimiter, head_rows, None)
            row_count, exact = parsed.rows, not parsed.stopped
        else:
            size, sha256 = fingerprint(path)
            with path.open("rb") as fh:
                first = fh.read(SNIFF_CHARS * 4)
                bom = first.startswith(UTF8_BOM)
                sniff_text, utf8 = _decode(first[3:] if bom else first)
                # A multi-byte character cut at the window edge is not an encoding error.
                if not utf8 and "�" not in sniff_text[:-4]:
                    utf8 = True
                delimiter = sniff_delimiter(sniff_text[:SNIFF_CHARS], False)
                fh.seek(len(UTF8_BOM) if bom else 0)
                stream = io.TextIOWrapper(fh, encoding="utf-8", errors="replace", newline="")
                lines = _CountingLines(stream)
                parsed = _parse(lines, delimiter, head_rows, sample_rows)
                consumed = lines.bytes
                stream.detach()
            if parsed.complete:
                row_count, exact = parsed.rows, not parsed.stopped
            else:
                body = size - (len(UTF8_BOM) if bom else 0)
                row_count = round(parsed.rows * body / max(1, consumed))
                exact = False
            utf8 = utf8 and not parsed.replaced

        warnings = list(parsed.warnings)
        if not utf8:
            warnings.insert(
                0,
                "The file is not valid UTF-8. Some characters could not be read and are "
                "shown as �. Save the file as UTF-8 and scan again.",
            )
        if not parsed.complete:
            warnings.append(
                f"This file is larger than {full_scan_max_bytes // 1_000_000} MB. Only the "
                f"first {parsed.rows} rows were read; the row count is an estimate and the "
                "column facts describe those rows only."
            )
        return DatasetPreview(
            format=self.id,
            size_bytes=size,
            sha256=sha256,
            columns=parsed.columns,
            row_count=row_count,
            row_count_exact=exact,
            rows_read=parsed.rows,
            head=parsed.head,
            details={
                "delimiter": delimiter,
                "delimiter_name": DELIMITER_NAMES[delimiter],
                "bom": bom,
                "utf8": utf8,
                "missing_tokens": list(MISSING_TOKENS),
            },
            warnings=warnings,
        )


class _CountingLines:
    """Lines of a text stream, counting their UTF-8 bytes (for a sampled row estimate)."""

    def __init__(self, stream: io.TextIOBase) -> None:
        self._stream = stream
        self.bytes = 0

    def __iter__(self) -> Iterator[str]:
        for line in self._stream:
            self.bytes += len(line.encode("utf-8", "replace"))
            yield line


@dataclass
class _Parsed:
    columns: list[ColumnProfile]
    rows: int
    head: list[list[str]]
    complete: bool  # every row was read (no sample limit reached)
    stopped: bool  # a parse error ended the read early
    replaced: bool
    warnings: list[str]


def _parse(stream: Iterable[str], delimiter: str, head_rows: int, limit: int | None) -> _Parsed:
    reader = csv.reader(stream, delimiter=delimiter)
    warnings: list[str] = []
    header: list[str] | None = None
    tallies: list[_Tally] = []
    head: list[list[str]] = []
    rows = 0
    blank: list[int] = []
    blank_total = 0
    ragged: list[int] = []
    ragged_total = 0
    replaced = False
    complete = True
    stopped = False
    start = 1
    try:
        for record in reader:
            line, start = start, reader.line_num + 1
            if not record or record == [""]:
                if header is not None:
                    blank_total += 1
                    if len(blank) < MAX_LINE_EXAMPLES:
                        blank.append(line)
                continue
            if header is None:
                header = record
                tallies = [_Tally() for _ in header]
                continue
            if limit is not None and rows >= limit:
                complete = False
                break
            rows += 1
            if len(head) < head_rows:
                head.append(record)
            if len(record) != len(header):
                ragged_total += 1
                if len(ragged) < MAX_LINE_EXAMPLES:
                    ragged.append(line)
            for i, tally in enumerate(tallies):
                value = record[i] if i < len(record) else ""
                if "�" in value:
                    replaced = True
                tally.push(value)
    except csv.Error as exc:
        warnings.append(
            f"Reading stopped at line {reader.line_num} ({exc}). Rows after it are not counted."
        )
        stopped = True

    if header is None:
        warnings.append("The file is empty.")
        return _Parsed([], 0, [], complete, stopped, replaced, warnings)

    names = list(header)
    dupes = sorted({n for n in names if names.count(n) > 1 and n.strip()})
    if dupes:
        warnings.append("Repeated column names: " + ", ".join(f"'{n}'" for n in dupes) + ".")
    empty = [i + 1 for i, n in enumerate(names) if not n.strip()]
    if empty:
        warnings.append(
            "Columns without a name at position " + ", ".join(str(i) for i in empty) + "."
        )
    if ragged_total:
        warnings.append(
            f"{ragged_total} row(s) have a different number of fields than the header "
            f"({len(header)}), at line(s) {_lines(ragged, ragged_total)}."
        )
    if blank_total:
        warnings.append(
            f"{blank_total} blank line(s) are not counted as rows, at line(s) "
            f"{_lines(blank, blank_total)}."
        )
    columns = [t.profile(n) for t, n in zip(tallies, names, strict=True)]
    return _Parsed(columns, rows, head, complete, stopped, replaced, warnings)
