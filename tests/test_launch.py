"""Controller core: loopback-only launch, /health, browser after health, the log (#9)."""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest

from launcher_helpers import running_controller
from opncafyr import cli
from opncafyr.core.logs import LOG_FILE, configure_logging, reset_logging


def _isolated_env(home: Path) -> dict[str, str]:
    """Point the app root (platformdirs) at ``home`` on every OS, with no runtime."""
    env = dict(os.environ)
    env.update(
        HOME=str(home),
        USERPROFILE=str(home),
        LOCALAPPDATA=str(home / "AppData" / "Local"),
        XDG_DATA_HOME=str(home / ".local" / "share"),
        OPNCAFYR_RUNTIME_FILE=str(home / "no-runtime.json"),
    )
    return env


def test_launch_no_browser_prints_the_url_and_serves_health(tmp_path):
    proc = subprocess.Popen(
        [sys.executable, "-m", "opncafyr", "launch", "--no-browser"],
        env=_isolated_env(tmp_path),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    lines: list[str] = []
    found: list[str] = []
    ready = threading.Event()

    def read() -> None:
        assert proc.stderr is not None
        for line in proc.stderr:
            lines.append(line)
            match = re.search(r"listening on (http://127\.0\.0\.1:\d+/)", line)
            if match and not found:
                found.append(match.group(1))
                ready.set()

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    try:
        assert ready.wait(60), "".join(lines)
        url = found[0]
        resp = None
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            with contextlib.suppress(httpx.TransportError):
                resp = httpx.get(f"{url}health")
                break
            time.sleep(0.1)
        assert resp is not None and resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["uptime_s"] >= 0
        assert set(body) == {"status", "version", "instance_id", "uptime_s"}
    finally:
        proc.terminate()
        proc.wait(30)
        reader.join(10)
    logs = list(tmp_path.rglob(LOG_FILE))
    assert len(logs) == 1
    records = [json.loads(line) for line in logs[0].read_text("utf-8").splitlines()]
    assert records and {"ts", "level", "logger", "message"} <= set(records[0])


@pytest.mark.parametrize("host", ["0.0.0.0", "::1", "::", "192.168.1.10", "localhost"])  # noqa: S104
def test_non_loopback_host_is_refused(host, monkeypatch, capsys):
    monkeypatch.setattr(cli, "run_controller", pytest.fail)
    with pytest.raises(SystemExit) as info:
        cli.main(["launch", "--no-browser", "--host", host])
    assert info.value.code != 0
    assert "listens only on 127.0.0.1" in capsys.readouterr().err


def test_loopback_host_is_accepted():
    args = cli.build_parser().parse_args(["launch", "--host", "127.0.0.1"])
    assert args.host == "127.0.0.1"


def test_two_launches_in_parallel_get_different_ports(tmp_path):
    # Separate state folders: two users, or two installs, on one machine.
    with (
        running_controller(tmp_path / "a") as first,
        running_controller(tmp_path / "b") as second,
    ):
        assert first["port"] != second["port"]
        for record in (first, second):
            health = httpx.get(f"http://127.0.0.1:{record['port']}/health").json()
            assert health["instance_id"] == record["instance_id"]


class _Resp:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_browser_opens_once_and_only_after_health(monkeypatch):
    events: list[str] = []
    attempts = iter([OSError("refused"), OSError("refused"), _Resp(), _Resp()])

    def urlopen(url, timeout):
        result = next(attempts)
        events.append("probe-ok" if isinstance(result, _Resp) else "probe-fail")
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(cli.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(cli.time, "sleep", lambda s: None)
    monkeypatch.setattr(cli.webbrowser, "open", lambda url: events.append(f"open {url}"))
    cli._open_when_healthy("http://127.0.0.1:5/")
    assert events == ["probe-fail", "probe-fail", "probe-ok", "open http://127.0.0.1:5/"]


def test_browser_never_opens_when_health_never_succeeds(monkeypatch, capsys):
    def urlopen(url, timeout):
        raise OSError("refused")

    opened: list[str] = []
    monkeypatch.setattr(cli.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(cli.webbrowser, "open", opened.append)
    cli._open_when_healthy("http://127.0.0.1:5/", timeout=0.3)
    assert opened == []
    assert "not opening a browser" in capsys.readouterr().err


def test_launcher_log_is_json_lines_from_app_and_uvicorn(tmp_path):
    path = configure_logging(tmp_path, console=False)
    try:
        logging.getLogger("opncafyr.test").info("job %s queued", "20260101T000000Z-abcdef")
        logging.getLogger("uvicorn.error").warning("server note")
    finally:
        reset_logging()
    records = [json.loads(line) for line in path.read_text("utf-8").splitlines()]
    assert [(r["logger"], r["level"], r["message"]) for r in records] == [
        ("opncafyr.test", "INFO", "job 20260101T000000Z-abcdef queued"),
        ("uvicorn.error", "WARNING", "server note"),
    ]
    assert not [h for h in logging.getLogger().handlers if hasattr(h, "_opncafyr_handler")]


def _free_port() -> int:
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_remote_launch_listens_on_loopback_only_and_prints_a_link(tmp_path):
    """#65: a native Linux VM. No container publishes a port, so the listener stays on the
    loopback; an SSH forward on the VM reaches it there. Foreign Hosts are still refused."""
    import psutil

    study = tmp_path / "Study"
    study.mkdir()
    port = _free_port()
    proc = subprocess.Popen(
        [
            sys.executable, "-m", "opncafyr", "launch", "--remote", "--port", str(port),
            "--workspace", str(study), "--local-port", "9100",
        ],
        env=_isolated_env(tmp_path),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )  # fmt: skip
    lines: list[str] = []
    opened = threading.Event()

    def read() -> None:
        assert proc.stderr is not None
        for line in proc.stderr:
            lines.append(line)
            if line.startswith("Open: "):
                opened.set()

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    try:
        assert opened.wait(60), "".join(lines)
        text = "".join(lines)
        assert "(remote, on this machine's loopback)" in text
        link = next(line for line in lines if line.startswith("Open: ")).split()[1]
        assert link.startswith("http://127.0.0.1:9100/open")  # the forward's port, not ours
        listening = [
            c.laddr
            for c in psutil.Process(proc.pid).net_connections(kind="inet")
            if c.status == psutil.CONN_LISTEN
        ]
        assert [(a.ip, a.port) for a in listening] == [("127.0.0.1", port)]
        ok = httpx.get(f"http://127.0.0.1:{port}/health", headers={"host": "127.0.0.1:9100"})
        assert ok.status_code == 200
        foreign = httpx.get(f"http://127.0.0.1:{port}/health", headers={"host": "evil.example"})
        assert foreign.status_code == 400
        assert "opncafyr-container" not in text  # native wording
    finally:
        proc.terminate()
        proc.wait(30)
        reader.join(10)
