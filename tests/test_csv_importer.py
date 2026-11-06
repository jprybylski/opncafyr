"""CSV importer: BOM, delimiters, missing tokens, type inference, sampling (#21)."""

import hashlib

import pytest

from opncafyr.workflows.nca.importers.csv import (
    MISSING_TOKENS,
    CsvImporter,
    sniff_delimiter,
)


def _read(path, cap=50_000_000, sample=100_000, head=50):
    return CsvImporter().read(path, head_rows=head, full_scan_max_bytes=cap, sample_rows=sample)


def _write(tmp_path, data: bytes, name="data.csv"):
    path = tmp_path / name
    path.write_bytes(data)
    return path


def _col(preview, name):
    return next(c for c in preview.columns if c.name == name)


def test_utf8_bom_is_stripped_and_reported(tmp_path):
    p = _read(_write(tmp_path, b"\xef\xbb\xbfUSUBJID,TIME\nS-1,0\nS-2,1\n"))
    assert [c.name for c in p.columns] == ["USUBJID", "TIME"]
    assert p.details["bom"] is True
    assert p.row_count == 2 and p.row_count_exact


def test_no_bom(tmp_path):
    p = _read(_write(tmp_path, b"A,B\n1,2\n"))
    assert p.details["bom"] is False


def test_semicolon_delimiter_with_decimal_commas(tmp_path):
    p = _read(_write(tmp_path, b"ID;CONC;TIME\n1;0,5;1,25\n2;1,5;2\n"))
    assert p.details["delimiter"] == ";"
    assert [c.name for c in p.columns] == ["ID", "CONC", "TIME"]
    assert p.head == [["1", "0,5", "1,25"], ["2", "1,5", "2"]]
    # A decimal comma is not a number; the value is kept as written.
    conc = _col(p, "CONC")
    assert conc.type == "character"
    assert [v.value for v in conc.values] == ["0,5", "1,5"]


@pytest.mark.parametrize("delimiter", [",", "\t", ";", "|"])
def test_sniffs_each_candidate(delimiter):
    d = delimiter
    assert sniff_delimiter(f"a{d}b{d}c\n1{d}2{d}3\n4{d}5{d}6\n", True) == d


def test_sniffing_respects_quotes():
    assert sniff_delimiter('name;note\n"Smith, J";"a, b, c"\nDoe;x\n', True) == ";"


def test_sniffing_ignores_a_line_cut_by_the_window():
    text = "a,b,c\n1,2,3\n4,5,6\n7;8"
    assert sniff_delimiter(text, False) == ","


def test_missing_tokens_are_documented_and_counted(tmp_path):
    assert MISSING_TOKENS == ("", "NA", ".", "NaN", "NULL")
    data = b"ID,CONC\n1,1.5\n2,.\n3,NA\n4,NaN\n5,NULL\n6,\n7, NA \n8,2\n"
    conc = _col(_read(_write(tmp_path, data)), "CONC")
    assert conc.type == "double"
    assert conc.n_missing == 6 and conc.n_values == 2
    assert (conc.minimum, conc.maximum) == (1.5, 2.0)
    # The raw values are still listed exactly as written.
    assert {v.value for v in conc.values} >= {".", "NA", " NA ", ""}


def test_na_text_is_not_a_number_in_an_integer_column(tmp_path):
    dose = _col(_read(_write(tmp_path, b"ID,DOSE\n1,100\n2,NA\n3,.\n")), "DOSE")
    assert dose.type == "integer"
    assert dose.n_missing == 2


@pytest.mark.parametrize(
    ("values", "expected", "fmt"),
    [
        (["1", "-2", "+3"], "integer", None),
        (["1", "2.5", "1e3", ".5"], "double", None),
        (["3000000000", "1"], "double", None),  # beyond R's integer range
        (["TRUE", "F", "true"], "logical", None),
        (["2026-01-05", "2026-1-6"], "date", "%Y-%m-%d"),
        (["25/12/2026", "13/01/2026"], "date", "%d/%m/%Y"),
        (["12/25/2026", "01/13/2026"], "date", "%m/%d/%Y"),
        (["2026-01-05T08:00", "2026-01-05T09:30"], "datetime", "%Y-%m-%dT%H:%M"),
        (["2026-01-05 08:00:01", "2026-01-05 09:30:00"], "datetime", "%Y-%m-%d %H:%M:%S"),
        (["08:30", "09:00"], "character", None),
        (["S-1", "S-2"], "character", None),
    ],
)
def test_type_inference(tmp_path, values, expected, fmt):
    body = "X\n" + "\n".join(values) + "\n"
    # A one-column file sniffs as a comma file; add a second column to be realistic.
    lines = body.splitlines()
    text = "\n".join(f"{line},k" for line in lines) + "\n"
    col = _col(_read(_write(tmp_path, text.encode())), "X")
    assert col.type == expected
    assert col.format == fmt


def test_ambiguous_day_month_is_noted(tmp_path):
    col = _col(_read(_write(tmp_path, b"D,k\n01/02/2026,1\n03/04/2026,2\n")), "D")
    assert col.type == "date" and col.format == "%d/%m/%Y"
    assert any("Day and month" in n for n in col.notes)


def test_entirely_missing_column_reads_as_logical(tmp_path):
    col = _col(_read(_write(tmp_path, b"A,B\n1,\n2,NA\n")), "B")
    assert col.type == "logical"
    assert col.n_values == 0 and col.n_missing == 2


def test_counterexamples_are_capped_at_three(tmp_path):
    rows = [str(i) for i in range(20)] + ["<LLOQ", "1.2.3", "BLQ", "n/a", "<LLOQ"]
    text = "CONC,k\n" + "".join(f"{v},1\n" for v in rows)
    col = _col(_read(_write(tmp_path, text.encode())), "CONC")
    assert col.type == "character"
    assert col.near_type == "number"
    assert col.misfits == ["<LLOQ", "1.2.3", "BLQ"]
    assert col.notes == ["Read as text: 20 of 25 values are numbers, but 5 are not."]


def test_mostly_text_column_has_no_near_type(tmp_path):
    col = _col(_read(_write(tmp_path, b"A,k\nx,1\ny,1\n3,1\n")), "A")
    assert col.type == "character"
    assert col.near_type is None and col.misfits == []


def test_values_are_capped(tmp_path):
    text = "ID,k\n" + "".join(f"S-{i},1\n" for i in range(60))
    p = _read(_write(tmp_path, text.encode()))
    assert _col(p, "ID").values_capped and _col(p, "ID").values == []
    assert not _col(p, "k").values_capped
    assert [(v.value, v.count) for v in _col(p, "k").values] == [("1", 60)]


def test_ragged_rows_and_blank_lines_are_reported(tmp_path):
    p = _read(_write(tmp_path, b"A,B,C\n1,2,3\n4,5\n\n6,7,8,9\n"))
    assert p.row_count == 3
    assert any("2 row(s) have a different number of fields" in w for w in p.warnings)
    assert any("at line(s) 3, 5" in w for w in p.warnings)
    assert any("1 blank line(s)" in w and "line(s) 4" in w for w in p.warnings)


def test_repeated_and_empty_header_names_are_reported(tmp_path):
    p = _read(_write(tmp_path, b"A,A,,B\n1,2,3,4\n"))
    assert any("Repeated column names: 'A'" in w for w in p.warnings)
    assert any("position 3" in w for w in p.warnings)


def test_crlf_and_quoted_newlines(tmp_path):
    p = _read(_write(tmp_path, b'A,NOTE\r\n1,"two\r\nlines"\r\n2,x\r\n'))
    assert p.row_count == 2
    assert p.head[0] == ["1", "two\r\nlines"]


def test_invalid_utf8_is_flagged(tmp_path):
    p = _read(_write(tmp_path, "ID,SITE\n1,Zürich\n".encode("latin-1")))
    assert p.details["utf8"] is False
    assert "not valid UTF-8" in p.warnings[0]
    assert p.row_count == 1


def test_empty_file(tmp_path):
    p = _read(_write(tmp_path, b""))
    assert p.columns == [] and p.row_count == 0
    assert p.warnings == ["The file is empty."]


def test_hash_and_size_match_the_bytes(tmp_path):
    data = b"\xef\xbb\xbfA;B\n1;2\n"
    p = _read(_write(tmp_path, data))
    assert p.sha256 == hashlib.sha256(data).hexdigest()
    assert p.size_bytes == len(data)


def test_head_rows_are_limited(tmp_path):
    text = "A,B\n" + "".join(f"{i},{i}\n" for i in range(10))
    p = _read(_write(tmp_path, text.encode()), head=3)
    assert p.head == [["0", "0"], ["1", "1"], ["2", "2"]]
    assert p.row_count == 10


def test_large_file_is_sampled_and_labelled(tmp_path):
    data = b"\xef\xbb\xbfID,CONC\n" + b"".join(b"S-%04d,%04d.5\n" % (i, i) for i in range(2000))
    path = _write(tmp_path, data)
    p = _read(path, cap=1000, sample=100)
    assert not p.row_count_exact and p.sampled
    assert p.rows_read == 100
    assert 1800 < p.row_count < 2200  # an estimate, labelled as one
    assert p.sha256 == hashlib.sha256(data).hexdigest()  # the whole file is hashed
    assert p.size_bytes == len(data)
    assert p.details["bom"] is True
    assert [c.name for c in p.columns] == ["ID", "CONC"]
    assert any("Only the first 100 rows were read" in w for w in p.warnings)


def test_large_file_read_to_the_end_is_exact(tmp_path):
    data = b"ID,CONC\n" + b"".join(b"S-%d,%d\n" % (i, i) for i in range(50))
    p = _read(_write(tmp_path, data), cap=10, sample=100)
    assert p.row_count_exact and p.row_count == 50
    assert not any("Only the first" in w for w in p.warnings)
