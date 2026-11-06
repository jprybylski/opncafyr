"""Single-instance lock and state record (#10)."""

from __future__ import annotations

import contextlib
import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

import psutil

from launcher_helpers import running_controller
from opncafyr import cli
from opncafyr.core import instance
from opncafyr.core.instance import InstanceLock, claim, clear_record, read_record, write_record
from opncafyr.core.proctree import free_loopback_port

HOLDER = Path(__file__).with_name("instance_holder.py")


def _write_raw_record(state: Path, *, pid: int, create_time: float, port: int) -> None:
    state.mkdir(parents=True, exist_ok=True)
    doc = {
        "schema_version": "1.0",
        "instance_id": "a" * 32,
        "pid": pid,
        "pid_create_time": create_time,
        "port": port,
        "started_at": "2026-01-01T00:00:00Z",
        "version": "0.0.0",
    }
    (state / instance.RECORD_FILE).write_text(json.dumps(doc))


@contextlib.contextmanager
def holder(state: Path, seconds: float):
    """A separate process holding the lock (another launcher, as far as we can tell)."""
    proc = subprocess.Popen(
        [sys.executable, str(HOLDER), str(state), str(seconds)], stdout=subprocess.PIPE
    )
    try:
        assert proc.stdout is not None
        assert proc.stdout.readline().strip() == b"locked"
        yield proc
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait(10)
        proc.stdout.close()


def test_second_launch_reopens_the_healthy_instance(tmp_path, monkeypatch):
    with running_controller(tmp_path) as first:
        opened: list[str] = []
        monkeypatch.setattr(cli.webbrowser, "open", opened.append)

        def no_bind(port):
            raise AssertionError("a second launch must not bind a port")

        monkeypatch.setattr(cli, "_bind_loopback", no_bind)
        assert cli.run_controller(state=tmp_path, claim_wait=5) == 0
        assert opened == [f"http://127.0.0.1:{first['port']}/"]
        assert read_record(tmp_path)["instance_id"] == first["instance_id"]

    # A clean stop clears the record and frees the lock.
    assert read_record(tmp_path) is None
    lock = InstanceLock(tmp_path)
    assert lock.try_acquire()
    lock.release()


def test_stale_record_is_recovered(tmp_path, caplog):
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait(30)
    _write_raw_record(tmp_path, pid=dead.pid, create_time=1.0, port=free_loopback_port())

    with (
        caplog.at_level(logging.INFO, logger="opncafyr.core.instance"),
        running_controller(tmp_path) as record,
    ):
        assert record["pid"] == os.getpid()
        assert record["instance_id"] != "a" * 32
    assert "stale state record" in caplog.text


def test_live_pid_without_the_lock_is_left_alone(tmp_path, caplog):
    # The record names a running process (here: this test) that is not a launcher, as
    # when a pid is reused. The lock is free, so a new instance starts; nothing is killed.
    me = psutil.Process()
    _write_raw_record(tmp_path, pid=me.pid, create_time=me.create_time(), port=free_loopback_port())

    with caplog.at_level(logging.WARNING, logger="opncafyr.core.instance"):
        claimed = claim(tmp_path, wait=1)
    try:
        assert claimed.lock is not None
    finally:
        claimed.lock.release()
    assert "probably reused" in caplog.text
    assert f"pid {me.pid}" in caplog.text


def test_a_held_lock_is_waited_for_never_broken(tmp_path):
    with holder(tmp_path, seconds=1.5) as proc:
        _write_raw_record(
            tmp_path,
            pid=proc.pid,
            create_time=psutil.Process(proc.pid).create_time(),
            port=free_loopback_port(),
        )
        started = time.monotonic()
        claimed = claim(tmp_path, wait=20, poll=0.1)
        try:
            assert claimed.lock is not None
            assert time.monotonic() - started >= 1.0
        finally:
            claimed.lock.release()
        # The holder ran to its own end and exited normally: it was not killed.
        assert proc.wait(10) == 0


def test_an_unresponsive_lock_holder_gives_a_diagnostic(tmp_path, monkeypatch, capsys):
    with holder(tmp_path, seconds=60) as proc:
        _write_raw_record(
            tmp_path,
            pid=proc.pid,
            create_time=psutil.Process(proc.pid).create_time(),
            port=free_loopback_port(),
        )

        def no_bind(port):
            raise AssertionError("must not bind while another launcher holds the lock")

        monkeypatch.setattr(cli, "_bind_loopback", no_bind)
        assert cli.run_controller(state=tmp_path, open_browser=False, claim_wait=0.5) == 1
        err = capsys.readouterr().err
        assert f"pid {proc.pid}" in err
        assert "was not stopped" in err
        assert proc.poll() is None  # still running


def test_lock_held_without_a_record(tmp_path):
    with holder(tmp_path, seconds=60):
        claimed = claim(tmp_path, wait=0.3, poll=0.1)
    assert claimed.lock is None and claimed.url is None
    assert "has not written its state record" in claimed.error


def test_record_is_validated_owned_and_private(tmp_path):
    record = write_record(tmp_path, instance_id="b" * 32, port=4321)
    assert read_record(tmp_path) == record
    assert "secret" not in json.dumps(record)
    if os.name == "posix":
        assert (tmp_path / instance.RECORD_FILE).stat().st_mode & 0o077 == 0
    assert clear_record(tmp_path, "c" * 32) is False  # not ours: kept
    assert read_record(tmp_path) is not None
    assert clear_record(tmp_path, "b" * 32) is True
    assert read_record(tmp_path) is None


def test_invalid_record_is_ignored(tmp_path):
    (tmp_path / instance.RECORD_FILE).write_text('{"pid": "x"}')
    assert read_record(tmp_path) is None
