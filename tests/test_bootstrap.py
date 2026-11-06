"""One-time open tokens (#11)."""

from __future__ import annotations

import os
import stat
import threading
import time

import pytest

from opncafyr.core.bootstrap import (
    TOKEN_TTL_S,
    consume_open_token,
    mint_open_token,
    open_url,
    sweep,
)


def test_a_token_works_once(tmp_path):
    token = mint_open_token(tmp_path)
    assert consume_open_token(tmp_path, token)
    assert not consume_open_token(tmp_path, token)
    assert list(tmp_path.iterdir()) == []


def test_only_the_hash_is_stored_and_private(tmp_path):
    folder = tmp_path / "open-tokens"
    token = mint_open_token(folder)
    (entry,) = folder.iterdir()
    assert token not in entry.name and token not in entry.read_text()
    if os.name != "nt":
        assert stat.S_IMODE(folder.stat().st_mode) & 0o077 == 0
        assert stat.S_IMODE(entry.stat().st_mode) == 0o600


def test_expired_tokens_are_refused(tmp_path):
    token = mint_open_token(tmp_path)
    assert not consume_open_token(tmp_path, token, now=time.time() + TOKEN_TTL_S + 1)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "token", [None, "", "short", "x" * 300, "../../etc/passwd", "a b c d e f g h i j k"]
)
def test_malformed_tokens_are_refused(tmp_path, token):
    mint_open_token(tmp_path)
    assert not consume_open_token(tmp_path, token)


def test_a_token_from_another_folder_is_refused(tmp_path):
    token = mint_open_token(tmp_path / "a")
    assert not consume_open_token(tmp_path / "b", token)


def test_racing_requests_get_one_session(tmp_path):
    token = mint_open_token(tmp_path)
    results: list[bool] = []
    start = threading.Barrier(8)

    def go():
        start.wait()
        results.append(consume_open_token(tmp_path, token))

    threads = [threading.Thread(target=go) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results.count(True) == 1


def test_sweep_removes_expired_files(tmp_path):
    mint_open_token(tmp_path)
    sweep(tmp_path, now=time.time() + TOKEN_TTL_S + 5)
    assert list(tmp_path.iterdir()) == []


def test_open_url():
    assert open_url("http://127.0.0.1:5/", "abc") == "http://127.0.0.1:5/open?token=abc"
