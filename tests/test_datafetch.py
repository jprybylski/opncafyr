"""External data through a managed datum (#83, ADR 0012), with a stand-in datum.

The stand-in (``fake_datum.py``) behaves like datum 1.6.0 for ``file`` sources and reads
``http``/``git`` bytes from a table, so these tests need no network. Like datum, it runs
``git``/``svn`` from PATH when its cwd is inside a working copy, which is what the
no-PATH-git test watches for.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from pathlib import Path

import pytest
import yaml

pytest.importorskip("opncafyr.workflows.datafetch")

from opncafyr.adapters.datum import approvals as appr  # noqa: E402
from opncafyr.adapters.datum import config as cfg  # noqa: E402
from opncafyr.adapters.datum import promote, state  # noqa: E402
from opncafyr.core.registry import load_descriptors  # noqa: E402
from opncafyr.core.runtime import Runtime  # noqa: E402
from publish_helpers import publish_client, wait_job  # noqa: E402

pytestmark = pytest.mark.skipif(os.name == "nt", reason="the stand-in datum is a shell script")
FAKE = Path(__file__).parent / "fake_datum.py"
TOKEN = "s3cr3t-T0KEN-value-123"  # noqa: S105 - a fixture secret the tests look for
CSV = b"ID,TIME,CONC\n1,0,0\n1,1,5.2\n"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fake_runtime(base: Path) -> Runtime:
    bin_dir = base / "bin"
    bin_dir.mkdir(parents=True)
    exe = bin_dir / "datum"
    exe.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{FAKE}" "{base}" "$@"\n', "utf-8")
    exe.chmod(0o755)
    return Runtime(executables={"datum": exe})


def calls(base: Path, command: str | None = None) -> list[dict]:
    seen = base / "seen.json"
    found = json.loads(seen.read_text("utf-8")) if seen.is_file() else []
    if command is None:
        return found
    return [c for c in found if command in c["argv"]]


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("OPNCAFYR_APP_ROOT", str(tmp_path / "app"))
    base = tmp_path / "fake"
    base.mkdir()
    study = tmp_path / "study"
    study.mkdir()
    return base, study, fake_runtime(base)


@pytest.fixture
def client(env, security, tmp_path):
    base, study, rt = env
    with publish_client(security, tmp_path, study, rt) as c:
        c.base = base
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if c.get("/api/datafetch/status").json()["available"]:
                break
            time.sleep(0.05)
        else:
            raise AssertionError("datum never became available")
        yield c


def write_config(study: Path, datasets: list[dict], **extra) -> None:
    doc = {"version": 1, **extra, "datasets": datasets}
    (study / ".data.yaml").write_text(yaml.safe_dump(doc, sort_keys=False), "utf-8")


def file_dataset(path: str = "upstream/pk.csv", target: str = "source-data/pk.csv", **kw):
    return {
        "id": "pk",
        "desc": "PK data",
        "source": {"type": "file", "path": path},
        "target": target,
        **kw,
    }


def fetch_and_review(client, ids=None, credentials=None) -> dict:
    body = {"credentials": credentials or {}}
    if ids is not None:
        body["ids"] = ids
    r = client.post("/api/datafetch/fetch", json=body)
    assert r.status_code == 200, r.text
    job = wait_job(client, r.json()["job_id"], timeout=60)
    assert job["state"] == "completed", job["message"]
    review = client.get(f"/api/datafetch/reviews/{r.json()['review_id']}")
    assert review.status_code == 200, review.text
    return review.json()


def study_bytes(study: Path, skip=(".opncafyr/jobs/", ".opncafyr/datafetch/state.json")) -> dict:
    out = {}
    for p in sorted(study.rglob("*")):
        rel = p.relative_to(study).as_posix()
        if p.is_file() and not any(rel.startswith(s) for s in skip):
            out[rel] = p.read_bytes()
    return out


# --- descriptor and boundaries -----------------------------------------------------------


def test_descriptor_is_a_binary_tool_with_two_jobs():
    tool = load_descriptors()["datum"]
    assert tool.runtime == "binary"
    assert tool.executables == ("datum",)
    assert tool.health.kind == "version_flag"
    assert set(tool.jobs) == {"data_check", "data_fetch"}
    assert {"data_fetch_local", "data_fetch_remote"} <= set(tool.capabilities)


def test_page_is_unavailable_without_datum(security, tmp_path, monkeypatch):
    monkeypatch.setenv("OPNCAFYR_APP_ROOT", str(tmp_path / "app"))
    study = tmp_path / "study"
    study.mkdir()
    with publish_client(security, tmp_path, study, Runtime()) as c:
        state_ = c.get("/api/datafetch/status").json()
        assert state_["installed"] and not state_["available"]
        assert "datum" in state_["detail"]
        assert c.post("/api/datafetch/check", json={}).status_code == 503
        # Nothing was created in the study.
        assert not (study / ".data.yaml").exists()


# --- the managed-mode policy ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("source", "target", "field", "code"),
    [
        (
            {"type": "command", "fetch_cmd": "x", "fingerprint_cmd": "y"},
            None,
            "source.type",
            "command",
        ),
        (None, "/etc/passwd", "target", "absolute"),
        (None, "source-data/../x.csv", "target", "parent"),
        (None, "results/x.csv", "target", "outside"),
        (None, ".opncafyr/x.csv", "target", "hidden"),
        (None, "source-data/${NAME}.csv", "target", "reference"),
        (
            {"type": "git", "url": "git@github.com:o/r.git", "ref": "main", "path": "a"},
            None,
            "source.url",
            "ssh",
        ),
        (
            {"type": "git", "url": "ssh://h/o/r.git", "ref": "main", "path": "a"},
            None,
            "source.url",
            "ssh",
        ),
        ({"type": "http", "url": "http://example.org/a.csv"}, None, "source.url", "not_https"),
        (
            {"type": "http", "url": "https://user:pw@example.org/a.csv"},
            None,
            "source.url",
            "credentials",
        ),
        ({"type": "http", "url": "https://${HOST}/a.csv"}, None, "source.url", "host"),
        (
            {"type": "http", "url": "https://example.org/a.csv?t=${PATH}"},
            None,
            "source.url",
            "reserved",
        ),
        ({"type": "file", "path": ".opncafyr/workspace.json"}, None, "source.path", "app_files"),
    ],
)
def test_policy_refuses_and_names_dataset_and_field(tmp_path, source, target, field, code):
    study = tmp_path / "study"
    (study / "source-data").mkdir(parents=True)
    ds = file_dataset(target=target or "source-data/pk.csv")
    if source is not None:
        ds["source"] = source
    result = cfg.check({"version": 1, "datasets": [ds]}, study)
    found = [(p.dataset, p.field, p.code) for p in result.problems]
    assert ("pk", field, code) in found
    for p in result.problems:
        assert "/etc/passwd" not in p.message and "user:pw" not in p.message


def test_symlinked_target_escaping_the_study_is_refused(tmp_path):
    study = tmp_path / "study"
    (study / "source-data").mkdir(parents=True)
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (study / "source-data" / "link").symlink_to(outside)
    result = cfg.check(
        {"version": 1, "datasets": [file_dataset(target="source-data/link/x.csv")]}, study
    )
    assert [(p.field, p.code) for p in result.problems] == [("target", "escape")]


def test_refused_config_never_starts_datum(client):
    study = client.study
    write_config(
        study,
        [
            file_dataset(source=None)
            | {
                "source": {
                    "type": "command",
                    "fetch_cmd": "touch /tmp/x",
                    "fingerprint_cmd": "date",
                }
            }
        ],
    )
    for route in ("check", "fetch"):
        r = client.post(f"/api/datafetch/{route}", json={})
        assert r.status_code == 400
        assert r.json()["detail"]["problems"][0]["dataset"] == "pk"
    assert calls(client.base, "check") == [] and calls(client.base, "fetch") == []


def test_credentials_must_be_declared_and_given(client):
    study = client.study
    write_config(
        study,
        [
            {
                "id": "web",
                "desc": "",
                "source": {"type": "http", "url": "https://example.org/a.csv?k=${API_KEY}"},
                "target": "source-data/a.csv",
            }
        ],
    )
    status = client.get("/api/datafetch/status").json()
    assert status["credentials"] == ["API_KEY"]
    missing = client.post("/api/datafetch/check", json={})
    assert missing.status_code == 400 and missing.json()["detail"]["missing"] == ["API_KEY"]
    extra = client.post(
        "/api/datafetch/check", json={"credentials": {"API_KEY": "x", "OTHER": "y"}}
    )
    assert extra.status_code == 400
    assert calls(client.base, "check") == []


# --- setup and editing ---------------------------------------------------------------------


def test_setup_writes_a_scaffold_once(client):
    study = client.study
    assert client.get("/api/datafetch/status").json()["exists"] is False
    assert client.post("/api/datafetch/setup", json={}).status_code == 200
    assert yaml.safe_load((study / ".data.yaml").read_text()) == {"version": 1, "datasets": []}
    (study / ".data.yaml").write_text("# mine\nversion: 1\ndatasets: []\n")
    assert client.post("/api/datafetch/setup", json={}).status_code == 409
    assert (study / ".data.yaml").read_text().startswith("# mine")


def test_add_edit_and_remove_a_dataset(client):
    study = client.study
    client.post("/api/datafetch/setup", json={})
    sha0 = client.get("/api/datafetch/status").json()["sha256"]
    form = {
        "id": "pk",
        "desc": "PK",
        "target": "source-data/pk.csv",
        "source": {"type": "https" if False else "http", "url": "https://example.org/pk.csv"},
    }
    bad = client.post(
        "/api/datafetch/datasets",
        json={"dataset": {**form, "target": "pk.csv"}, "expected_sha256": sha0},
    )
    assert bad.status_code == 400 and bad.json()["detail"]["problems"][0]["field"] == "target"
    r = client.post("/api/datafetch/datasets", json={"dataset": form, "expected_sha256": sha0})
    assert r.status_code == 200, r.text
    sha1 = r.json()["sha256"]
    stale = client.post(
        "/api/datafetch/datasets", json={"dataset": {**form, "id": "pk2"}, "expected_sha256": sha0}
    )
    assert stale.status_code == 409  # someone else's change is never overwritten
    r = client.put(
        "/api/datafetch/datasets/pk",
        json={"dataset": {**form, "policy": "update"}, "expected_sha256": sha1},
    )
    assert r.status_code == 200, r.text
    data = yaml.safe_load((study / ".data.yaml").read_text())
    assert data["datasets"][0]["policy"] == "update"
    (study / ".data.yaml").write_text("# keep me\n" + (study / ".data.yaml").read_text())
    status = client.get("/api/datafetch/status").json()
    assert status["has_comments"] is True
    r = client.post("/api/datafetch/datasets/pk/remove", json={"expected_sha256": status["sha256"]})
    assert r.status_code == 200 and r.json()["offer_forget"] is False
    assert yaml.safe_load((study / ".data.yaml").read_text())["datasets"] == []


def test_validate_reports_live_problems(client):
    client.post("/api/datafetch/setup", json={})
    form = {
        "id": "pk",
        "target": "source-data/pk.csv",
        "source": {"type": "http", "url": "http://x.org/a"},
    }
    r = client.post("/api/datafetch/validate", json={"dataset": form})
    assert [p["code"] for p in r.json()["problems"]] == ["not_https"]


# --- fetch, review, apply ---------------------------------------------------------------------


def test_fetch_changes_nothing_until_apply(client):
    study = client.study
    (study / "upstream").mkdir()
    (study / "upstream" / "pk.csv").write_bytes(CSV)
    write_config(study, [file_dataset()])
    before = study_bytes(study)
    review = fetch_and_review(client)
    assert study_bytes(study, skip=(".opncafyr/",)) == {
        k: v for k, v in before.items() if not k.startswith(".opncafyr/")
    }
    [ds] = review["datasets"]
    assert ds["status"] == "fetched"
    assert ds["files"] == [
        {
            "path": "source-data/pk.csv",
            "action": "new",
            "current_sha256": None,
            "current_size": None,
            "new_sha256": sha(CSV),
            "new_size": len(CSV),
        }
    ]
    r = client.post(f"/api/datafetch/reviews/{review['review_id']}/apply")
    assert r.status_code == 200, r.text
    assert (study / "source-data" / "pk.csv").read_bytes() == CSV
    assert (study / ".data.lock.yaml").is_file()
    doc = state.read(study)
    assert doc["datasets"]["pk"]["files"] == [
        {"path": "source-data/pk.csv", "sha256": sha(CSV), "size": len(CSV)}
    ]
    # The review folder is gone, and the lockfile names no absolute or snapshot paths.
    assert not (study / ".opncafyr/datafetch/reviews" / review["review_id"]).exists()
    assert str(study) not in (study / ".data.lock.yaml").read_text()
    # The dataset scan lists the file with the datum label; import records its hash.
    files = {f["path"]: f for f in client.get("/api/datasets").json()["files"]}
    assert "source-data/pk.csv" in files
    preview = client.post("/api/datasets/preview", json={"path": "source-data/pk.csv"}).json()[
        "dataset"
    ]
    assert preview["annotations"]["datafetch"]["dataset"] == "pk"
    assert preview["annotations"]["datafetch"]["matches_fetched"] is True
    imported = client.post("/api/datasets/import", json={"path": "source-data/pk.csv"}).json()
    assert imported["source"]["sha256"] == sha(CSV)


def test_refetch_keeps_replaced_bytes_and_imports_stay_pinned(client):
    study = client.study
    (study / "upstream").mkdir()
    (study / "upstream" / "pk.csv").write_bytes(CSV)
    write_config(study, [file_dataset()])
    client.post(f"/api/datafetch/reviews/{fetch_and_review(client)['review_id']}/apply")
    first = client.post("/api/datasets/import", json={"path": "source-data/pk.csv"}).json()[
        "source"
    ]
    changed = CSV + b"1,2,3.1\n"
    (study / "upstream" / "pk.csv").write_bytes(changed)
    review = fetch_and_review(client)
    [f] = review["datasets"][0]["files"]
    assert f["action"] == "replace" and f["current_sha256"] == sha(CSV)
    client.post(f"/api/datafetch/reviews/{review['review_id']}/apply").raise_for_status()
    kept = study / ".opncafyr/datafetch/previous/pk" / sha(CSV)
    assert kept.read_bytes() == CSV
    assert (study / "source-data/pk.csv").read_bytes() == changed
    # The earlier import still pins the old bytes; the scan shows the file changed since.
    ws = json.loads((study / ".opncafyr/workspace.json").read_text())
    assert first in ws["sources"]
    files = {f["path"]: f for f in client.get("/api/datasets").json()["files"]}
    assert files["source-data/pk.csv"]["imported"] is False
    sizes = client.get("/api/datafetch/status").json()["previous"]
    assert sizes == {"pk": {"files": 1, "bytes": len(CSV)}}
    assert client.post("/api/datafetch/datasets/pk/purge").status_code == 200
    assert not kept.exists()


def test_apply_refuses_when_the_study_file_changed_after_the_fetch(client):
    study = client.study
    (study / "upstream").mkdir()
    (study / "upstream" / "pk.csv").write_bytes(CSV)
    write_config(study, [file_dataset()])
    review = fetch_and_review(client)
    (study / "source-data").mkdir(exist_ok=True)
    (study / "source-data" / "pk.csv").write_bytes(b"hand made\n")
    r = client.post(f"/api/datafetch/reviews/{review['review_id']}/apply")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "file_changed"
    assert (study / "source-data" / "pk.csv").read_bytes() == b"hand made\n"
    assert client.delete(f"/api/datafetch/reviews/{review['review_id']}").status_code == 200


@pytest.mark.parametrize(
    "stop_at", ["journal", "previous", "move:source-data/pk.csv", "lock", "state"]
)
@pytest.mark.parametrize("recover", ["finish", "rollback"])
def test_interrupted_apply_is_flagged_and_recovers(client, monkeypatch, stop_at, recover):
    study = client.study
    (study / "upstream").mkdir()
    (study / "upstream" / "pk.csv").write_bytes(CSV)
    write_config(study, [file_dataset()])
    client.post(
        f"/api/datafetch/reviews/{fetch_and_review(client)['review_id']}/apply"
    ).raise_for_status()
    old_lock = (study / ".data.lock.yaml").read_bytes()
    changed = CSV + b"2,0,0\n"
    (study / "upstream" / "pk.csv").write_bytes(changed)
    review = fetch_and_review(client)

    class Crash(Exception):
        pass

    def step(name):
        if name == stop_at:
            raise Crash(name)

    monkeypatch.setattr(promote, "_step", step)
    with pytest.raises(Crash):
        client.post(f"/api/datafetch/reviews/{review['review_id']}/apply")
    monkeypatch.setattr(promote, "_step", lambda name: None)
    status = client.get("/api/datafetch/status").json()
    assert status["interrupted"]["review_id"] == review["review_id"]
    # A new fetch waits until the interrupted Apply is resolved.
    assert client.post("/api/datafetch/fetch", json={}).status_code == 409
    r = client.post("/api/datafetch/recover", json={"action": recover})
    assert r.status_code == 200, r.text
    assert client.get("/api/datafetch/status").json()["interrupted"] is None
    current = (study / "source-data" / "pk.csv").read_bytes()
    if recover == "finish":
        assert current == changed
        assert state.read(study)["datasets"]["pk"]["files"][0]["sha256"] == sha(changed)
    else:
        assert current == CSV
        assert (study / ".data.lock.yaml").read_bytes() == old_lock


def test_directory_sync_touches_only_the_datasets_files(client):
    study = client.study
    up = study / "upstream" / "dir"
    (up / "sub").mkdir(parents=True)
    (up / "a.txt").write_text("a")
    (up / "sub" / "b.txt").write_text("b")
    write_config(study, [file_dataset(path="upstream/dir", target="source-data/d")])
    client.post(
        f"/api/datafetch/reviews/{fetch_and_review(client)['review_id']}/apply"
    ).raise_for_status()
    (study / "source-data/d/mine.txt").write_text("not from datum")
    (up / "a.txt").unlink()
    (up / "c.txt").write_text("c")
    (up / "sub" / "b.txt").write_text("b2")
    review = fetch_and_review(client)
    actions = {f["path"]: f["action"] for f in review["datasets"][0]["files"]}
    assert actions == {
        "source-data/d/c.txt": "new",
        "source-data/d/sub/b.txt": "replace",
        "source-data/d/a.txt": "remove",
    }
    client.post(f"/api/datafetch/reviews/{review['review_id']}/apply").raise_for_status()
    d = study / "source-data/d"
    assert sorted(p.relative_to(d).as_posix() for p in d.rglob("*") if p.is_file()) == [
        "c.txt",
        "mine.txt",
        "sub/b.txt",
    ]
    assert (d / "mine.txt").read_text() == "not from datum"
    assert (study / ".opncafyr/datafetch/previous/pk" / sha(b"a")).read_bytes() == b"a"


# --- check ----------------------------------------------------------------------------------


def test_check_is_report_only_even_under_update(client):
    study = client.study
    (study / "upstream").mkdir()
    (study / "upstream" / "pk.csv").write_bytes(CSV)
    write_config(study, [file_dataset(policy="update")])
    client.post(
        f"/api/datafetch/reviews/{fetch_and_review(client)['review_id']}/apply"
    ).raise_for_status()
    (study / "upstream" / "pk.csv").write_bytes(CSV + b"9,9,9\n")
    before = study_bytes(study)
    r = client.post("/api/datafetch/check", json={})
    job = wait_job(client, r.json()["job_id"], timeout=60)
    assert job["state"] == "completed", job["message"]
    assert study_bytes(study) == before
    last = state.read(study)["last_check"]
    assert last["results"][0]["status"] == "updated"
    status = client.get("/api/datafetch/status").json()
    assert status["datasets"][0]["last_check"]["status"] == "updated"


def test_datum_never_runs_git_or_svn(client, tmp_path):
    study = client.study
    (study / ".git").mkdir()  # the study is a Git working copy
    (study / ".svn").mkdir()  # and an SVN one
    (study / ".gitignore").write_text("keep\n")
    sentinel = tmp_path / "vcs-ran"
    bin_dir = client.base / "bin"  # first on the datum process's PATH
    for tool in ("git", "svn"):
        exe = bin_dir / tool
        exe.write_text(f'#!/bin/sh\necho "{tool} $*" >> "{sentinel}"\nexit 1\n')
        exe.chmod(0o755)
    (study / "upstream").mkdir()
    (study / "upstream" / "pk.csv").write_bytes(CSV)
    write_config(study, [file_dataset()])
    job = wait_job(client, client.post("/api/datafetch/check", json={}).json()["job_id"])
    assert job["state"] == "completed", job["message"]
    review = fetch_and_review(client)
    client.post(f"/api/datafetch/reviews/{review['review_id']}/apply").raise_for_status()
    assert not sentinel.exists()
    assert (study / ".gitignore").read_text() == "keep\n"
    for call in calls(client.base, "fetch") + calls(client.base, "check"):
        cwd = Path(call["cwd"]).resolve()
        assert not cwd.is_relative_to(study.resolve())
    # Control: the stand-in does run PATH git when its cwd is in the study.
    import subprocess

    subprocess.run(
        [str(bin_dir / "datum"), "--json", "check"],
        cwd=study,
        env={"PATH": str(bin_dir)},
        check=False,
        capture_output=True,
    )  # noqa: S603
    assert sentinel.read_text().startswith("git ")


# --- secrets --------------------------------------------------------------------------------


def test_credentials_never_leak(client, security):
    study = client.study
    url = "https://data.example.org/pk.csv?token=${TOKEN}"
    expanded = url.replace("${TOKEN}", TOKEN)
    (client.base / "remote.json").write_text(json.dumps({expanded: CSV.decode()}))
    (client.base / "mode.txt").write_text("echo-secret")
    write_config(
        study,
        [
            {
                "id": "web",
                "desc": "",
                "source": {
                    "type": "http",
                    "url": url,
                    "headers": {"Authorization": "Bearer ${TOKEN}"},
                },
                "target": "source-data/pk.csv",
            },
            {
                "id": "gone",
                "desc": "",
                "source": {
                    "type": "http",
                    "url": "https://data.example.org/missing.csv?t=${TOKEN}",
                },
                "target": "source-data/m.csv",
            },
        ],
    )
    review = fetch_and_review(client, credentials={"TOKEN": TOKEN})
    statuses = {d["id"]: d["status"] for d in review["datasets"]}
    assert statuses == {"web": "fetched", "gone": "error"}
    client.post(f"/api/datafetch/reviews/{review['review_id']}/apply").raise_for_status()
    check = client.post("/api/datafetch/check", json={"credentials": {"TOKEN": TOKEN}}).json()
    wait_job(client, check["job_id"])

    secret = TOKEN.encode()
    for path in study.rglob("*"):
        if path.is_file() and path.name != "pk.csv":
            assert secret not in path.read_bytes(), path
    assert TOKEN not in json.dumps(review)
    assert TOKEN not in json.dumps(client.get("/api/jobs").json())
    assert TOKEN not in json.dumps(client.get("/api/datafetch/status").json())
    for call in calls(client.base, "fetch") + calls(client.base, "check"):
        assert not any(TOKEN in a for a in call["argv"])
        env = call["env"]
        assert env["TOKEN"] == TOKEN
        allowed = {
            "TOKEN",
            "NO_COLOR",
            "XDG_CACHE_HOME",
            "PATH",
            "TMPDIR",
            "TMP",
            "TEMP",
            "HOME",
            "USER",
            "LOGNAME",
            "LANG",
            "TZ",
            "PWD",
            "SHLVL",
            "_",
            "OLDPWD",
            "__CF_USER_TEXT_ENCODING",
        }
        extra = {k for k in env if k not in allowed and not k.startswith("LC_")}
        assert extra == set(), extra
        assert not any(k.startswith(("SSH_", "GIT_")) for k in env)
        assert (
            Path(env["XDG_CACHE_HOME"])
            .resolve()
            .is_relative_to((Path(os.environ["OPNCAFYR_APP_ROOT"])).resolve())
        )


# --- file sources outside the study -----------------------------------------------------------


def test_outside_file_source_needs_approval_kept_outside_the_study(client, tmp_path):
    study = client.study
    share = tmp_path / "share"
    share.mkdir()
    (share / "pk.csv").write_bytes(CSV)
    write_config(study, [file_dataset(path=str(share / "pk.csv"))])
    status = client.get("/api/datafetch/status").json()
    assert status["datasets"][0]["approval"] == {"path": str(share / "pk.csv"), "approved": False}
    r = client.post("/api/datafetch/fetch", json={})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "approval_needed"
    assert calls(client.base, "fetch") == []
    wrong = client.post("/api/datafetch/datasets/pk/approve", json={"path": "/etc/passwd"})
    assert wrong.status_code == 409
    client.post(
        "/api/datafetch/datasets/pk/approve", json={"path": str(share / "pk.csv")}
    ).raise_for_status()
    review = fetch_and_review(client)
    assert review["datasets"][0]["files"][0]["new_sha256"] == sha(CSV)
    store = Path(os.environ["OPNCAFYR_APP_ROOT"]) / "state" / "datafetch" / "approvals.json"
    assert store.is_file()
    assert not any("approv" in p.name for p in study.rglob("*"))


def test_approval_does_not_follow_a_copied_study(tmp_path):
    store = appr.Approvals(tmp_path / "approvals.json")
    here = appr.study_key(tmp_path / "a", "2026-01-01T00:00:00Z")
    copy = appr.study_key(tmp_path / "b", "2026-01-01T00:00:00Z")
    store.approve(here, "pk", "/share/pk.csv")
    assert store.approved(here, "pk", "/share/pk.csv")
    assert not store.approved(copy, "pk", "/share/pk.csv")
    assert not store.approved(here, "pk", "/share/other.csv")
    assert not store.approved(here, "pk2", "/share/pk.csv")


# --- delete, undelete, forget -------------------------------------------------------------------


def test_delete_keeps_a_copy_and_undelete_and_forget_edit_the_lock(client):
    study = client.study
    (study / "upstream").mkdir()
    (study / "upstream" / "pk.csv").write_bytes(CSV)
    write_config(study, [file_dataset()])
    client.post(
        f"/api/datafetch/reviews/{fetch_and_review(client)['review_id']}/apply"
    ).raise_for_status()
    r = client.post("/api/datafetch/datasets/pk/delete")
    assert r.status_code == 200, r.text
    assert r.json()["removed"] == ["source-data/pk.csv"]
    assert not (study / "source-data/pk.csv").exists()
    assert (study / ".opncafyr/datafetch/previous/pk" / sha(CSV)).read_bytes() == CSV
    lock = yaml.safe_load((study / ".data.lock.yaml").read_text())
    assert lock["items"]["pk"]["deleted"] is True
    assert client.get("/api/datafetch/status").json()["datasets"][0]["audit"]["status"] == "deleted"
    client.post("/api/datafetch/datasets/pk/undelete").raise_for_status()
    assert "deleted" not in yaml.safe_load((study / ".data.lock.yaml").read_text())["items"]["pk"]
    client.post("/api/datafetch/datasets/pk/forget").raise_for_status()
    assert "pk" not in yaml.safe_load((study / ".data.lock.yaml").read_text())["items"]
