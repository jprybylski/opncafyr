"""Fetching a standards source with the pinned Git (#70, ADR 0011).

The "pinned Git" here is a stand-in that runs the system Git, with one change: the https URL
of ``git fetch`` is pointed at a local repository, so the whole job (fetch, exact commit,
blobs read byte for byte, inspection, install) runs without a network.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from opncafyr.core import gitfetch as G
from opncafyr.core import standards as S
from opncafyr.core.runtime import Runtime
from publish_helpers import publish_client, wait_job, writable
from standards_helpers import PROFILE, deck_files, mixed_bundle

SYSTEM_GIT = shutil.which("git")
needs_git = pytest.mark.skipif(
    SYSTEM_GIT is None or os.name == "nt", reason="needs a system Git and a POSIX shell"
)
TOKEN = "ghp_" + "Z" * 36

WRAPPER = r"""
import json, os, subprocess, sys
here, real, repo = sys.argv[1], sys.argv[2], sys.argv[3]
args = sys.argv[4:]
env = os.environ.get
log = {"argv": args, "auth": env("GIT_CONFIG_VALUE_0"), "key": env("GIT_CONFIG_KEY_0"),
       "global": env("GIT_CONFIG_GLOBAL"), "prompt": env("GIT_TERMINAL_PROMPT")}
if "fetch" in args:
    with open(os.path.join(here, "fetch.json"), "w") as fh:
        json.dump(log, fh)
    i = args.index("--")
    args = args[:i + 1] + [repo] + args[i + 2:]
    args = ["-c", "protocol.file.allow=always"] + args
sys.exit(subprocess.call([real, *args]))
"""


def git(cwd, *args):
    subprocess.run([SYSTEM_GIT, *args], cwd=cwd, check=True, capture_output=True)


def make_repo(tmp_path: Path) -> tuple[Path, str]:
    """A repository with a bundle under bundles/house/, a deck template under deck/, and a
    link next to them."""
    repo = tmp_path / "upstream"
    mixed_bundle(repo / "bundles" / "house")
    for path, data in deck_files().items():
        (repo / "deck" / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / "deck" / path).write_bytes(data)
    (repo / "deck" / "link.yaml").symlink_to("/etc/hosts")
    git(repo, "init", "-q", "-b", "main")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@example.org", "add", "-A")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@example.org", "commit", "-qm", "v1")
    git(repo, "tag", "v1.0.0")
    head = subprocess.run(
        [SYSTEM_GIT, "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()
    return repo, head


def fake_git(tmp_path: Path, repo: Path) -> Path:
    bin_dir = tmp_path / "pinned" / "bin"
    bin_dir.mkdir(parents=True)
    script = tmp_path / "pinned" / "wrapper.py"
    script.write_text(WRAPPER, "utf-8")
    exe = bin_dir / "git"
    exe.write_text(
        f'#!/bin/sh\nexec "{sys.executable}" "{script}" "{tmp_path / "pinned"}" '
        f'"{SYSTEM_GIT}" "{repo}" "$@"\n',
        "utf-8",
    )
    exe.chmod(0o755)
    return exe


@pytest.fixture
def api(security, tmp_path):
    repo, head = make_repo(tmp_path)
    study = (tmp_path / "Study").resolve()
    study.mkdir()
    runtime = Runtime(executables={"git": fake_git(tmp_path, repo)})
    with publish_client(security, tmp_path, study, runtime) as client:
        client.head = head
        client.pinned = tmp_path / "pinned"
        yield client
    writable(study)


# --- the source and the lock-down ----------------------------------------------------------


@pytest.mark.parametrize(
    ("repo", "url"),
    [
        ("org/house-standards", "https://github.com/org/house-standards.git"),
        ("ghe.example.org/org/house", "https://ghe.example.org/org/house.git"),
        ("https://ghe.example.org/org/house.git", "https://ghe.example.org/org/house.git"),
    ],
)
def test_sources_resolve_to_https(repo, url):
    assert G.resolve_source(repo, None, None).url == url


@pytest.mark.parametrize(
    "repo",
    [
        "https://user:tok@github.com/org/r",
        "git@github.com:org/r.git",
        "ssh://github.com/org/r",
        "http://github.com/org/r",
        "file:///tmp/r",
        "org",
    ],
)
def test_other_sources_are_refused(repo):
    with pytest.raises(G.FetchError):
        G.resolve_source(repo, None, None)


@pytest.mark.parametrize("ref", ["-upload-pack=x", "a..b", "main.lock", "x y"])
def test_bad_refs_are_refused(ref):
    with pytest.raises(G.FetchError):
        G.resolve_source("org/r", ref, None)


def test_lockdown_reads_no_user_configuration(tmp_path):
    prefix, env = G.lockdown(Path("/opt/git/bin/git"), tmp_path)
    flags = dict(prefix[i + 1].split("=", 1) for i, a in enumerate(prefix) if a == "-c")
    assert flags["credential.helper"] == "" and flags["core.askPass"] == ""
    assert flags["protocol.allow"] == "never" and flags["protocol.https.allow"] == "always"
    assert flags["http.followRedirects"] == "false"
    assert Path(env["GIT_CONFIG_GLOBAL"]).read_text() == ""
    assert env["GIT_TERMINAL_PROMPT"] == "0"


def test_the_token_goes_in_a_header_for_that_url_only():
    env = G.auth_env("https://ghe.example.org/org/r.git", TOKEN)
    assert env["GIT_CONFIG_KEY_0"] == "http.https://ghe.example.org/org/r.git.extraHeader"
    assert env["GIT_CONFIG_VALUE_0"].startswith("Authorization: Basic ")
    assert TOKEN not in env["GIT_CONFIG_VALUE_0"]
    assert G.auth_env("https://x/y.git", None) == {}


def test_failures_are_explained():
    assert "token" in G.diagnose_text("fatal: Authentication failed for 'https://…'")
    assert "can't be reached" in G.diagnose_text("Could not resolve host: ghe.example.org")
    assert "no such branch" in G.diagnose_text("fatal: couldn't find remote ref nope")


def test_fetch_needs_the_pinned_git(client, tmp_path):
    study = tmp_path / "S"
    study.mkdir()
    from opncafyr.core.workspace import init_workspace, open_workspace

    init_workspace(study, "0.0.1")
    client.app.state.workspace = open_workspace(study)
    client.headers.update(client.session_headers)
    assert client.get("/api/standards").json()["git_available"] is False
    r = client.post("/api/standards/fetch", json={"repo": "org/house"})
    assert r.status_code == 501 and r.json()["detail"]["code"] == "git_unavailable"


# --- the job ---------------------------------------------------------------------------------


def fetch(api, **body):
    r = api.post("/api/standards/fetch", json=body)
    assert r.status_code == 200, r.text
    out = r.json()
    job = wait_job(api, out["job_id"], 60)
    return out, job


@needs_git
def test_a_bundle_is_fetched_inspected_and_installed(api):
    out, job = fetch(
        api, repo="ghe.example.org/org/standards", ref="v1.0.0", subdir="bundles/house", token=TOKEN
    )
    assert job["state"] == "completed", job["message"]
    preview = api.post(
        "/api/standards/inspect", json={"source": {"kind": "fetched", "id": out["fetch_id"]}}
    ).json()
    assert preview["problems"] == [], preview["problems"]
    assert preview["source"]["commit"] == api.head
    assert preview["source"]["ref"] == "v1.0.0"
    assert preview["source"]["kind"] == "git"
    done = api.post("/api/standards/install", json={"preview_id": preview["preview_id"]}).json()
    assert (api.study / "profiles/dose-escalation.yaml").read_bytes() == PROFILE
    record = json.loads((api.study / S.STATE_FILE).read_text("utf-8"))["snapshots"][
        done["snapshot"]
    ]
    assert record["source"]["commit"] == api.head
    assert record["source"]["subdir"] == "bundles/house"
    assert not (api.study / S.STORE / S.FETCHED / out["fetch_id"]).exists()  # removed

    # The token reached Git as a header, never on the command line, and is stored nowhere.
    seen = json.loads((api.pinned / "fetch.json").read_text("utf-8"))
    assert seen["auth"].startswith("Authorization: Basic ")
    assert TOKEN not in json.dumps(seen["argv"])
    assert seen["prompt"] == "0"
    for path in api.study.rglob("*"):
        if path.is_file():
            data = path.read_bytes()
            assert TOKEN.encode() not in data, path
            assert seen["auth"].split()[-1].encode() not in data, path
    record = api.get(f"/api/jobs/{out['job_id']}").json()["job"]
    assert TOKEN not in json.dumps(record)


@needs_git
def test_a_native_template_is_fetched_without_its_links(api):
    out, job = fetch(api, repo="org/templates", subdir="deck", template={"tool": "deckifyr"})
    assert job["state"] == "completed", job["message"]
    meta = json.loads(
        (api.study / S.STORE / S.FETCHED / out["fetch_id"] / "fetch.json").read_text()
    )
    assert meta["commit"] == api.head and meta["ref"] == "HEAD"
    files = api.study / S.STORE / S.FETCHED / out["fetch_id"] / "files"
    assert sorted(p.name for p in files.iterdir()) == sorted(deck_files())
    preview = api.post(
        "/api/standards/inspect",
        json={
            "source": {"kind": "fetched", "id": out["fetch_id"]},
            "template": {"tool": "deckifyr"},
            "expect": {"commit": api.head},
        },
    ).json()
    assert preview["problems"] == []
    wrong = api.post(
        "/api/standards/inspect",
        json={
            "source": {"kind": "fetched", "id": out["fetch_id"]},
            "template": {"tool": "deckifyr"},
            "expect": {"commit": "0" * 40},
        },
    ).json()
    assert any("commit" in p for p in wrong["problems"])


@needs_git
def test_a_missing_ref_fails_with_words(api):
    _, job = fetch(api, repo="org/templates", ref="no-such-branch")
    assert job["state"] == "failed"
    assert "no such branch" in job["message"]


@needs_git
def test_a_folder_without_standards_yaml_fails(api):
    _, job = fetch(api, repo="org/templates", subdir="deck")
    assert job["state"] == "failed"
    assert "standards.yaml" in job["message"]


def test_a_token_with_spaces_is_refused(api):
    r = api.post("/api/standards/fetch", json={"repo": "org/r", "token": "abc def ghi"})
    assert r.status_code == 400 and r.json()["detail"]["code"] == "bad_token"
