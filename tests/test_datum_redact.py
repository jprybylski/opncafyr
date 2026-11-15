"""Redaction of datum's messages (#83): entered values, URL queries and credentials."""

import base64

import pytest

pytest.importorskip("opncafyr.adapters.datum")

from opncafyr.adapters.datum.redact import MASK, Redactor, source_display  # noqa: E402
from opncafyr.adapters.datum.staging import parse_results  # noqa: E402

SECRET = "tok-123456789"  # noqa: S105 - a fixture value


def test_values_queries_and_userinfo_are_masked():
    r = Redactor([SECRET])
    text = r.text(
        f'fetch: Get "https://h.org/a.csv?token={SECRET}": refused; '
        f"Authorization: Bearer {SECRET}; https://u:{SECRET}@h.org/x; "
        + base64.b64encode(SECRET.encode()).decode()
    )
    assert SECRET not in text
    assert base64.b64encode(SECRET.encode()).decode() not in text
    assert "https://h.org/a.csv?" + MASK in text
    assert f"https://{MASK}@h.org/x" in text


def test_a_query_is_masked_even_without_a_known_secret():
    assert Redactor().text("https://h.org/a?key=raw") == f"https://h.org/a?{MASK}"


def test_parse_results_redacts_and_explains_an_unreachable_source():
    out = parse_results(
        '{"results": [{"id": "web", "status": "error", "message": '
        '"fetch: Get \\"https://127.0.0.1:9/p.csv?token=abc\\": dial tcp: connection refused"}]}',
        Redactor(["abc"]),
    )
    [r] = out
    assert "abc" not in r["message"]
    assert r["message"].startswith("The source could not be reached.")


def test_source_display_shows_host_and_path_only():
    assert (
        source_display({"type": "http", "url": "https://u:p@h.org/a/b.csv?k=v#x"})
        == "h.org/a/b.csv"
    )
    assert (
        source_display(
            {"type": "git", "url": "https://h.org/o/r.git", "ref": "v1", "path": "d.csv"}
        )
        == "h.org/o/r.git @ v1: d.csv"
    )
