"""Subject IDs and secrets never reach the launcher's logs (#11, ADR 0007 section 4)."""

from __future__ import annotations

import csv
import json
import logging
from pathlib import Path

import pytest

from opncafyr.core.logs import configure_logging, reset_logging
from opncafyr.core.redaction import REDACTED, RedactionFilter, Redactor
from toy_adapter import toy_supervisor

FIXTURE = Path(__file__).parent / "fixtures" / "two_analyte.csv"
ID_COLUMNS = ("USUBJID", "SUBJID")


def _fixture_ids() -> set[str]:
    with FIXTURE.open(newline="", encoding="utf-8") as fh:
        return {row[c] for row in csv.DictReader(fh) for c in ID_COLUMNS}


@pytest.fixture
def redactor():
    r = Redactor()
    assert r.register_from_csv(FIXTURE, ID_COLUMNS) == len(_fixture_ids())
    return r


@pytest.fixture
def log_file(tmp_path, redactor):
    path = configure_logging(tmp_path / "logs", console=False, filters=[RedactionFilter(redactor)])
    yield path
    reset_logging()


def test_whole_tokens_only(redactor):
    assert redactor.redact("subject TA-001 at 08:00") == f"subject {REDACTED} at 08:00"
    assert redactor.redact("SUBJID 001.") == f"SUBJID {REDACTED}."
    # Not inside a longer identifier or number.
    assert redactor.redact("run 20260101T000000Z-001abc, 1001 rows") == (
        "run 20260101T000000Z-001abc, 1001 rows"
    )


def test_query_string_secrets_are_masked():
    text = '"GET /open?token=abc-DEF_123 HTTP/1.1" 303'
    assert Redactor().redact(text) == f'"GET /open?token={REDACTED} HTTP/1.1" 303'


def test_subject_ids_never_reach_the_log_file(log_file):
    ids = sorted(_fixture_ids())
    rows = [{"USUBJID": i, "CONC": 1.0} for i in ids[:3]]
    log = logging.getLogger("opncafyr.test")
    log.info("imported %d rows; columns %s", 168, "USUBJID, CONC")
    log.warning("duplicate key for subject %s at row %d", ids[0], 12)
    log.info(f"f-string with {ids[1]} and {ids[2]}")
    log.info("rows: %s", rows)
    log.info("by name: %(subject)s", {"subject": ids[3]})
    try:
        raise ValueError(f"bad value for {ids[4]}")
    except ValueError:
        log.exception("check failed")
    logging.getLogger("uvicorn.access").info(
        '%s - "%s %s HTTP/%s" %d',
        "127.0.0.1:5",
        "GET",
        "/open?token=abcdefghijklmnopqrstuvwxyz",
        "1.1",
        303,
    )

    # Only what the code wrote: the timestamp's digits could look like a SUBJID.
    records = [json.loads(line) for line in log_file.read_text("utf-8").splitlines()]
    text = "\n".join(r["message"] + r.get("exception", "") for r in records)
    for subject in ids:
        assert subject not in text.replace(REDACTED, "")
    assert "abcdefghijklmnopqrstuvwxyz" not in text
    # What logs may carry is still there.
    assert "imported 168 rows; columns USUBJID, CONC" in text
    assert "at row 12" in text
    assert "<3 rows redacted>" in text
    assert "ValueError" in text


def test_child_output_is_redacted_in_its_log(tmp_path, redactor):
    # USUBJIDs ("TA-001"): pids and ports in the log can't contain them by chance.
    ids = sorted(i for i in _fixture_ids() if "-" in i)
    sup, _ = toy_supervisor(tmp_path, redactor=redactor)
    try:
        sup.start("toy", {"say": [f"loaded subject {ids[0]}", f"{ids[1]},{ids[2]},12.5"]})
    finally:
        sup.stop_all()
    text = (tmp_path / "logs" / "toy.log").read_text("utf-8")
    assert "TOY_READY" in text
    assert f"loaded subject {REDACTED}\n" in text
    assert f"{REDACTED},{REDACTED},12.5\n" in text
    assert not [i for i in ids if i in text]
