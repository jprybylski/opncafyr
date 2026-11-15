"""The managed mode against the real, pinned datum binary (#83).

Skipped unless ``OPNCAFYR_TEST_DATUM`` names a datum 1.6.0 executable (CI downloads the
pinned Linux asset and checks its SHA-256 first). It covers what the stand-in can't
prove: datum's own config handling, a ``file`` source fetch and apply, a report-only
check under the ``update`` policy, no PATH ``git``/``svn`` when the study is a working
copy, and an ``https`` source from a local server (Linux: Go reads ``SSL_CERT_FILE``).
"""

from __future__ import annotations

import http.server
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
    pytest.mark.skipif(not DATUM, reason="set OPNCAFYR_TEST_DATUM to a datum 1.6.0 binary"),
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
    assert out.stdout.strip() == "datum v1.6.0"
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
    assert real.get("/api/datafetch/status").json()["datasets"][0]["audit"]["status"] == "ok"

    (study / "upstream" / "pk.csv").write_bytes(CSV + b"1,2,3\n")
    lock = (study / ".data.lock.yaml").read_bytes()
    job = wait_job(real, real.post("/api/datafetch/check", json={}).json()["job_id"], 120)
    assert job["state"] == "completed", job["message"]
    assert (study / "source-data" / "pk.csv").read_bytes() == CSV  # report-only
    assert (study / ".data.lock.yaml").read_bytes() == lock
    assert (
        real.get("/api/datafetch/status").json()["datasets"][0]["last_check"]["status"] == "updated"
    )

    assert not real.sentinel.exists()
    assert (study / ".gitignore").read_text() == "keep\n"


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Go reads SSL_CERT_FILE on Linux")
def test_https_source_from_a_local_server(real, tmp_path, monkeypatch):
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

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - http.server API
            self.send_response(200)
            self.send_header("ETag", '"v1"')
            self.send_header("Content-Length", str(len(CSV)))
            self.end_headers()
            self.wfile.write(CSV)

        do_HEAD = do_GET  # noqa: N815

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        url = f"https://127.0.0.1:{server.server_address[1]}/pk.csv"
        write_config(
            real.study,
            [
                {
                    "id": "web",
                    "desc": "",
                    "source": {"type": "http", "url": url},
                    "target": "source-data/web.csv",
                }
            ],
        )
        fetch_apply(real)
        assert (real.study / "source-data" / "web.csv").read_bytes() == CSV
    finally:
        server.shutdown()


def test_an_unreachable_https_source_fails_fast_and_changes_nothing(real):
    import socket

    with socket.socket() as s:  # a local port nothing listens on: like a cut network
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    write_config(
        real.study,
        [
            {
                "id": "web",
                "desc": "",
                "source": {"type": "http", "url": f"https://127.0.0.1:{port}/pk.csv"},
                "target": "source-data/web.csv",
            }
        ],
    )
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
