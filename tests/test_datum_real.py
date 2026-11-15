"""The managed mode against the real, pinned datum binary (#83, #85).

Skipped unless ``OPNCAFYR_TEST_DATUM`` names a datum 1.7.0 executable (CI downloads the
pinned asset and checks its SHA-256 first). It covers what the stand-in can't prove:

- datum's own config handling;
- a staged ``file`` source fetch and apply, with per-file hashes in the lockfile and
  audit drift;
- a ``--report-only`` check under the ``update`` policy;
- delete, undelete and forget through datum's JSON;
- no PATH ``git``/``svn`` when the study is a working copy (``--no-vcs``);
- datum itself refusing what the launcher's policy refuses;
- an ``https`` source from a local server, and a refused https-to-http redirect (Linux:
  Go reads ``SSL_CERT_FILE``).
"""

from __future__ import annotations

import http.server
import json
import os
import shutil
import ssl
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
import yaml

pytest.importorskip("opncafyr.workflows.datafetch")

from opncafyr.core.runtime import Runtime  # noqa: E402
from publish_helpers import publish_client, wait_job  # noqa: E402

DATUM = os.environ.get("OPNCAFYR_TEST_DATUM")
pytestmark = [
    pytest.mark.skipif(not DATUM, reason="set OPNCAFYR_TEST_DATUM to a datum 1.7.0 binary"),
    pytest.mark.skipif(os.name == "nt", reason="sentinel tools are shell scripts"),
]
CSV = b"ID,TIME,CONC\n1,0,0\n1,1,5.2\n"


@pytest.fixture
def real(tmp_path, monkeypatch, security):
    monkeypatch.setenv("OPNCAFYR_APP_ROOT", str(tmp_path / "app"))
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    exe = bin_dir / "datum"
    shutil.copy2(DATUM, exe)
    sentinel = tmp_path / "vcs-ran"
    for tool in ("git", "svn"):  # next to datum, so first on its PATH
        fake = bin_dir / tool
        fake.write_text(f'#!/bin/sh\necho "{tool} $*" >> "{sentinel}"\nexit 1\n')
        fake.chmod(0o755)
    out = subprocess.run([str(exe), "--version"], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "datum v1.7.0"
    study = tmp_path / "study"
    study.mkdir()
    with publish_client(security, tmp_path, study, Runtime(executables={"datum": exe})) as c:
        deadline = time.monotonic() + 30
        while not c.get("/api/datafetch/status").json()["available"]:
            assert time.monotonic() < deadline
            time.sleep(0.05)
        c.sentinel = sentinel
        yield c


def write_config(study: Path, datasets: list[dict]) -> None:
    doc = {"version": 1, "datasets": datasets}
    (study / ".data.yaml").write_text(yaml.safe_dump(doc, sort_keys=False), "utf-8")


def fetch_apply(client) -> dict:
    r = client.post("/api/datafetch/fetch", json={})
    assert r.status_code == 200, r.text
    job = wait_job(client, r.json()["job_id"], timeout=120)
    assert job["state"] == "completed", job["message"]
    review = client.get(f"/api/datafetch/reviews/{r.json()['review_id']}").json()
    client.post(f"/api/datafetch/reviews/{review['review_id']}/apply").raise_for_status()
    return review


def test_file_source_fetch_check_and_no_vcs(real):
    study = real.study
    (study / ".git").mkdir()
    (study / ".svn").mkdir()
    (study / ".gitignore").write_text("keep\n")
    (study / "upstream").mkdir()
    (study / "upstream" / "pk.csv").write_bytes(CSV)
    write_config(
        study,
        [
            {
                "id": "pk",
                "desc": "PK",
                "source": {"type": "file", "path": "upstream/pk.csv"},
                "target": "source-data/pk.csv",
                "policy": "update",
                "ignore": True,
            }
        ],
    )
    status = real.get("/api/datafetch/status").json()
    assert status["problems"] == [], status["problems"]
    assert status["datasets"][0]["audit"]["status"] == "pending"
    review = fetch_apply(real)
    assert review["datasets"][0]["files"][0]["action"] == "new"
    assert (study / "source-data" / "pk.csv").read_bytes() == CSV
    item = yaml.safe_load((study / ".data.lock.yaml").read_text())["items"]["pk"]
    assert item["size"] == len(CSV)
    entry = real.get("/api/datafetch/status").json()["datasets"][0]
    assert entry["audit"]["status"] == "ok" and entry["audit"]["drift"] == "ok"
    assert entry["matches_fetched"] is True

    (study / "upstream" / "pk.csv").write_bytes(CSV + b"1,2,3\n")
    lock = (study / ".data.lock.yaml").read_bytes()
    job = wait_job(real, real.post("/api/datafetch/check", json={}).json()["job_id"], 120)
    assert job["state"] == "completed", job["message"]
    assert (study / "source-data" / "pk.csv").read_bytes() == CSV  # report-only
    assert (study / ".data.lock.yaml").read_bytes() == lock
    last = real.get("/api/datafetch/status").json()["datasets"][0]["last_check"]
    assert last["status"] == "stale" and last["message"].startswith("An update is available")

    (study / "source-data" / "pk.csv").write_bytes(b"edited\n")
    entry = real.get("/api/datafetch/status").json()["datasets"][0]
    assert entry["audit"]["drift"] == "changed" and entry["matches_fetched"] is False

    assert not real.sentinel.exists()
    assert (study / ".gitignore").read_text() == "keep\n"


def test_directory_fetch_records_each_file_and_lock_edits_use_json(real):
    study = real.study
    up = study / "upstream" / "dir"
    (up / "sub").mkdir(parents=True)
    (up / "a.csv").write_bytes(CSV)
    (up / "sub" / "b.csv").write_bytes(CSV + b"2,1,1\n")
    write_config(
        study,
        [
            {
                "id": "d",
                "desc": "",
                "source": {"type": "file", "path": "upstream/dir"},
                "target": "source-data/d",
            }
        ],
    )
    (study / "source-data" / "d").mkdir(parents=True)
    (study / "source-data" / "d" / "mine.txt").write_text("not from datum")
    fetch_apply(real)
    item = yaml.safe_load((study / ".data.lock.yaml").read_text())["items"]["d"]
    assert [f["path"] for f in item["dir_files"]] == ["a.csv", "sub/b.csv"]
    (up / "a.csv").unlink()
    review = fetch_apply(real)
    actions = {f["path"]: f["action"] for f in review["datasets"][0]["files"]}
    assert actions == {"source-data/d/a.csv": "remove", "source-data/d/sub/b.csv": "unchanged"}
    d = study / "source-data" / "d"
    assert sorted(p.relative_to(d).as_posix() for p in d.rglob("*") if p.is_file()) == [
        "mine.txt",
        "sub/b.csv",
    ]
    assert real.get("/api/datafetch/status").json()["datasets"][0]["audit"]["drift"] == "ok"
    r = real.post("/api/datafetch/datasets/d/delete")
    assert r.status_code == 200, r.text
    assert r.json()["removed"] == ["source-data/d/sub/b.csv"]
    assert (d / "mine.txt").is_file()
    lock = yaml.safe_load((study / ".data.lock.yaml").read_text())
    assert lock["items"]["d"]["deleted"] is True
    real.post("/api/datafetch/datasets/d/undelete").raise_for_status()
    assert "deleted" not in yaml.safe_load((study / ".data.lock.yaml").read_text())["items"]["d"]
    real.post("/api/datafetch/datasets/d/forget").raise_for_status()
    assert "d" not in yaml.safe_load((study / ".data.lock.yaml").read_text())["items"]
    r = real.post("/api/datafetch/datasets/d/forget")
    assert r.status_code >= 400  # datum reports "skipped": nothing in the lock
    assert not real.sentinel.exists()


SSH_GIT = {"type": "git", "url": "git@example.org:a/b.git", "ref": "main", "path": "x"}


@pytest.mark.parametrize(
    "source,target",
    [
        ({"type": "command", "command": "echo hi > out.csv"}, "source-data/x.csv"),
        ({"type": "http", "url": "http://127.0.0.1:9/x.csv"}, "source-data/x.csv"),
        (SSH_GIT, "source-data/x.csv"),
        ({"type": "http", "url": "https://127.0.0.1:9/x.csv?k=${HOME}"}, "source-data/x.csv"),
        ({"type": "file", "path": "upstream/pk.csv"}, "../outside.csv"),
        ({"type": "file", "path": "upstream/pk.csv"}, "elsewhere/x.csv"),
    ],
)  # fmt: skip
def test_datum_itself_refuses_what_the_policy_refuses(real, tmp_path, source, target):
    """Defence in depth: the adapter's flags make datum refuse a config the launcher's own
    check would refuse, before it contacts anything or writes anything."""
    from opncafyr.adapters import datum

    rt = Runtime(executables={"datum": Path(DATUM)})
    ds = {"id": "x", "desc": "", "source": source, "target": target}
    config = tmp_path / "c.yaml"
    config.write_text(yaml.safe_dump({"version": 1, "datasets": [ds]}))
    for command, extra in (("check", ["--report-only"]), ("fetch", [])):
        args = datum.argv(rt, config, tmp_path / "l.yaml", command, root=real.study, extra=extra)
        done = subprocess.run(args, capture_output=True, text=True, check=False, cwd=tmp_path)  # noqa: S603
        assert done.returncode == 2, (source, target, done.stdout)
        assert "error" in json.loads(done.stdout)
    assert not (tmp_path / "l.yaml").exists()
    assert not (tmp_path / "out.csv").exists()
    assert not (real.study / "source-data").exists()


LINUX_TLS = pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="Go reads SSL_CERT_FILE on Linux"
)


def serve(handler, tmp_path=None, monkeypatch=None) -> http.server.ThreadingHTTPServer:
    """A local server on 127.0.0.1; with ``tmp_path``, over TLS with a fresh certificate
    that datum trusts through ``SSL_CERT_FILE``."""
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    if tmp_path is not None:
        openssl = shutil.which("openssl")
        if openssl is None:
            pytest.skip("needs openssl to make a test certificate")
        cert, key = tmp_path / "cert.pem", tmp_path / "key.pem"
        subprocess.run(  # noqa: S603 - fixed argv, test only
            [openssl, "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
             "-subj", "/CN=127.0.0.1", "-addext", "subjectAltName=IP:127.0.0.1",
             "-keyout", str(key), "-out", str(cert)],
            check=True, capture_output=True,
        )  # fmt: skip
        monkeypatch.setenv("SSL_CERT_FILE", str(cert))  # passed through to datum
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert, key)
        server.socket = context.wrap_socket(server.socket, server_side=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


class CSVHandler(http.server.BaseHTTPRequestHandler):
    hits: list[str] = []

    def do_GET(self):  # noqa: N802 - http.server API
        self.hits.append(self.path)
        self.send_response(200)
        self.send_header("ETag", '"v1"')
        self.send_header("Content-Length", str(len(CSV)))
        self.end_headers()
        self.wfile.write(CSV)

    do_HEAD = do_GET  # noqa: N815

    def log_message(self, *args):
        pass


def web_dataset(url: str) -> dict:
    source = {"type": "http", "url": url}
    return {"id": "web", "desc": "", "source": source, "target": "source-data/web.csv"}


@LINUX_TLS
def test_https_source_from_a_local_server(real, tmp_path, monkeypatch):
    server = serve(CSVHandler, tmp_path, monkeypatch)
    try:
        write_config(
            real.study, [web_dataset(f"https://127.0.0.1:{server.server_address[1]}/pk.csv")]
        )
        fetch_apply(real)
        assert (real.study / "source-data" / "web.csv").read_bytes() == CSV
    finally:
        server.shutdown()


@LINUX_TLS
def test_an_https_to_http_redirect_is_refused(real, tmp_path, monkeypatch):
    """U10 (datum#44): the configured URL is https, but the server redirects to http."""

    class Plain(CSVHandler):
        hits: list[str] = []

    plain = serve(Plain)
    target = f"http://127.0.0.1:{plain.server_address[1]}/pk.csv"

    class Redirect(CSVHandler):
        def do_GET(self):  # noqa: N802 - http.server API
            self.send_response(302)
            self.send_header("Location", target)
            self.send_header("Content-Length", "0")
            self.end_headers()

        do_HEAD = do_GET  # noqa: N815

    server = serve(Redirect, tmp_path, monkeypatch)
    try:
        write_config(
            real.study, [web_dataset(f"https://127.0.0.1:{server.server_address[1]}/pk.csv")]
        )
        r = real.post("/api/datafetch/fetch", json={})
        job = wait_job(real, r.json()["job_id"], timeout=120)
        if job["state"] == "completed":
            review = real.get(f"/api/datafetch/reviews/{r.json()['review_id']}").json()
            [ds] = review["datasets"]
            assert ds["status"] == "error" and ds["files"] == []
        assert Plain.hits == []
        assert not (real.study / "source-data" / "web.csv").exists()
    finally:
        server.shutdown()
        plain.shutdown()


def test_an_unreachable_https_source_fails_fast_and_changes_nothing(real):
    import socket

    with socket.socket() as s:  # a local port nothing listens on: like a cut network
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    write_config(real.study, [web_dataset(f"https://127.0.0.1:{port}/pk.csv")])
    started = time.monotonic()
    r = real.post("/api/datafetch/fetch", json={})
    job = wait_job(real, r.json()["job_id"], timeout=120)
    assert time.monotonic() - started < 60
    review = real.get(f"/api/datafetch/reviews/{r.json()['review_id']}")
    if job["state"] == "completed":
        [ds] = review.json()["datasets"]
        assert ds["status"] == "error" and ds["files"] == []
        assert ds.get("message")
    else:
        assert job["message"]
    assert not (real.study / "source-data" / "web.csv").exists()
