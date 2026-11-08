"""#56: gitneighbr behind /apps/gitneighbr/, with the pinned Git, against a study folder.

Needs a runtime with ``rscript``, ``git`` and gitneighbr >= 0.2.0 in its R library
(``prepare_runtime.py --gitneighbr-tarball ... --git ...``).
"""

from __future__ import annotations

import hashlib
import subprocess

import httpx
import pytest
from proxy_harness import EVIL, assert_stays_under, load_page, tree_pids, wait_gone

from opncafyr.core.supervisor import ChildState
from opncafyr.workflows.nca import history

pytestmark = pytest.mark.integration
ROUTE = "/apps/gitneighbr/"
API = ROUTE + "api/v1/"


@pytest.fixture(scope="module")
def ready(runtime):
    lib = runtime.r_library
    if not (runtime.has("rscript") and runtime.has("git") and lib is not None):
        pytest.skip("runtime has no Rscript/git/gitneighbr")
    if not (lib / "gitneighbr" / "scripts" / "serve.R").is_file():
        pytest.skip("gitneighbr isn't installed in the runtime's R library")
    return runtime


def _study(root):
    (root / ".opncafyr" / "runs" / "20261106T120000Z-ab12").mkdir(parents=True)
    run_file = root / ".opncafyr" / "runs" / "20261106T120000Z-ab12" / "run.json"
    run_file.write_text('{"status": "completed"}\n')
    (root / ".opncafyr" / "recipes").mkdir()
    (root / ".opncafyr" / "recipes" / "clean.yaml").write_text("steps: []\n")
    (root / "source-data").mkdir()
    (root / "source-data" / "pk.csv").write_text("USUBJID,CONC\n1,2\n")
    (root / "notes.md").write_text("notes\n")
    return root


def _digest(root):
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file() and ".git" not in p.relative_to(root).parts
    }


@pytest.fixture(scope="module")
def study(tmp_path_factory):
    return _study(tmp_path_factory.mktemp("study"))


@pytest.fixture(scope="module")
def session(controller, study):
    return {
        "workspace_root": str(study),
        "git_config": str(controller.root / "state" / "gitneighbr" / "gitconfig"),
        "policy": history.policy(),
    }


@pytest.fixture(scope="module")
def child(ready, controller, session):
    child = controller.supervisor.start("gitneighbr", session)
    yield child
    controller.supervisor.stop("gitneighbr")


def _post(c, path, body=None):
    version = c.get(API + "status").json()["status_version"]
    return c.post(API + path, json={**(body or {}), "status_version": version})


def test_page_and_assets_stay_under_the_prefix(browser, controller, child):
    record = load_page(browser, controller, ROUTE)
    assert_stays_under(record, controller, ROUTE)
    assert any("/api/v1/status" in u for u in record.requests), "the page made no status call"
    token = child.spec.auth_headers["X-Gitneighbr-Token"]
    assert not any(token in u for u in record.requests)


def test_proxy_needs_the_session(controller, child):
    url = controller.origin + API + "status"
    assert httpx.get(url).status_code == 403
    with controller.client() as c:
        assert c.get(API + "status", headers={"sec-fetch-site": "cross-site"}).status_code == 403
        assert c.post(API + "init", json={}, headers={"origin": EVIL}).status_code == 403


def test_direct_child_port_attacks_fail(child):
    origin = child.context.origin
    token = child.spec.auth_headers
    form = httpx.post(
        origin + API + "init",
        data={"x": "1"},
        headers={**token, "origin": EVIL, "sec-fetch-site": "cross-site"},
    )
    assert form.status_code == 403
    rebind = httpx.get(origin + API + "status", headers={**token, "host": "evil.example"})
    assert rebind.status_code == 400
    assert httpx.get(origin + API + "status").status_code == 401
    assert httpx.get(origin + API + "status", headers=token).status_code == 200
    # The shutdown route needs the second token, which never leaves the adapter.
    assert httpx.post(origin + API + "shutdown", headers=token).status_code == 403


def test_git_actions_wait_for_running_jobs(controller, child, monkeypatch):
    monkeypatch.setattr(controller.app.state.jobs, "active", lambda: True)
    with controller.client() as c:
        resp = _post(c, "init")
    assert resp.status_code == 423
    assert resp.json()["error"]["code"] == "jobs_active"


def test_set_up_and_save_a_selected_snapshot(controller, child, study, runtime):
    before = _digest(study)
    with controller.client() as c:
        status = c.get(API + "status").json()["data"]
        assert status["primary_state"] == "NOT_REPOSITORY"
        assert status["managed"] is True and status["features"] == ["local"]
        rules = [t["rule"] for t in status["layout"]["ignore_template"]]
        init = _post(c, "init", {"ignore_rules": [".opncafyr/jobs/", ".DS_Store"]})
        assert init.json()["ok"] is True, init.text
        identity = _post(c, "identity", {"name": "Analyst", "email": "a@example.com"})
        assert identity.json()["ok"] is True, identity.text

        changes = {ch["path"]: ch for ch in c.get(API + "changes").json()["data"]["changes"]}
        assert changes[".opncafyr/recipes/clean.yaml"]["group"] == "sources"
        assert changes[".opncafyr/runs/20261106T120000Z-ab12/run.json"]["protected"] is True
        assert changes["source-data/pk.csv"]["review"] == ["study-data"]

        chosen = {"paths": [".opncafyr/recipes/clean.yaml"], "summary": "Add recipe"}
        commit = _post(c, "commit", chosen)
        assert commit.json()["ok"] is True, commit.text
    assert ".opncafyr/jobs/" in rules
    git = str(runtime.executable("git"))
    shown = subprocess.run(  # noqa: S603 - test, argv only
        [git, "-C", str(study), "show", "--name-only", "--format=", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert shown.returncode == 0, shown.stderr
    assert shown.stdout.split() == [".opncafyr/recipes/clean.yaml"]
    after = _digest(study)
    # Every pre-existing file, the run record included, is byte-for-byte unchanged.
    assert {k: v for k, v in after.items() if k in before} == before
    assert set(after) - set(before) == {".gitignore"}
    # The identity went to the launcher's file, never the study or the user's own config.
    config = controller.root / "state" / "gitneighbr" / "gitconfig"
    assert config.read_text().count("Analyst") == 1


def test_remote_routes_are_off_unless_asked(controller, child):
    with controller.client() as c:
        resp = _post(c, "remote/check")
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "FEATURE_DISABLED"


def test_a_study_inside_another_repository_is_not_exposed(ready, controller, tmp_path):
    outer = tmp_path / "outer"
    outer.mkdir()
    git = str(ready.executable("git"))
    subprocess.run([git, "init", "-q", str(outer)], check=True)  # noqa: S603 - test
    (outer / "secret.txt").write_text("outer\n")
    study = _study(outer / "study")
    sup = controller.supervisor
    sup.start(
        "gitneighbr",
        {
            "workspace_root": str(study),
            "git_config": str(tmp_path / "cfg"),
            "policy": history.policy(),
        },
    )
    try:
        with controller.client() as c:
            status = c.get(API + "status").json()["data"]
            assert status["primary_state"] == "ENCLOSING_REPOSITORY"
            changes = c.get(API + "changes").json()
            assert changes["ok"] is False
            assert "secret.txt" not in changes.__repr__()
            init = _post(c, "init")
            assert init.json()["error"]["code"] == "ENCLOSING_REPOSITORY"
        assert not (study / ".git").exists()
    finally:
        sup.stop("gitneighbr")


def test_opening_another_workspace_stops_it(ready, controller, session, tmp_path):
    sup = controller.supervisor
    sup.start("gitneighbr", session)
    assert sup.stop_bound_elsewhere(tmp_path) == ["gitneighbr"]
    assert sup.state("gitneighbr") is ChildState.STOPPED


def test_orderly_stop_and_tree_kill(ready, controller, session):
    sup = controller.supervisor
    child = sup.start("gitneighbr", session)
    procs = tree_pids(child.pid)
    sup.stop("gitneighbr")
    assert child.exit_code == 0
    assert not wait_gone(procs)
