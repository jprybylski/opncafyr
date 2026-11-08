"""#56: the gitneighbr adapter and the Version history workflow (no R or Git needed)."""

import json
import re
from pathlib import Path

import httpx
import pytest

from opncafyr.adapters.gitneighbr import (
    API_TOKEN_HEADER,
    SHUTDOWN_TOKEN_ENV,
    SHUTDOWN_TOKEN_HEADER,
    GitneighbrWebApp,
    git_exec_path,
)
from opncafyr.core.adapter import ChildActivity, LaunchContext, SupportsActivity
from opncafyr.core.registry import load_descriptors
from opncafyr.core.runtime import Runtime
from opncafyr.workflows.nca import history


def _exe(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("")
    return path


@pytest.fixture
def runtime(tmp_path):
    lib = tmp_path / "lib"
    _exe(lib / "gitneighbr" / "scripts" / "serve.R")
    git_root = tmp_path / "app" / "git"
    (git_root / "libexec" / "git-core").mkdir(parents=True)
    return Runtime(
        executables={
            "rscript": _exe(tmp_path / "R" / "bin" / "Rscript"),
            "git": _exe(git_root / "bin" / "git"),
        },
        r_library=lib,
    )


def _ctx(tmp_path, runtime):
    workdir = tmp_path / "state" / "children" / "gitneighbr-1"
    workdir.mkdir(parents=True, exist_ok=True)
    return LaunchContext(
        tool="gitneighbr",
        port=50002,
        base_path="/apps/gitneighbr/",
        workdir=workdir,
        tmpdir=workdir / "tmp",
        runtime=runtime,
        token="t" * 43,
    )


@pytest.fixture
def study(tmp_path):
    root = tmp_path / "study"
    (root / ".opncafyr").mkdir(parents=True)
    return root


def session(tmp_path, study, **extra):
    return {
        "workspace_root": str(study),
        "git_config": str(tmp_path / "state" / "gitneighbr" / "gitconfig"),
        "policy": history.policy(),
        **extra,
    }


def test_descriptor_guards_mutations_and_needs_the_pinned_git():
    tool = load_descriptors()["gitneighbr"]
    assert tool.web_route == "/apps/gitneighbr/"
    assert tool.guard_mutations is True
    assert set(tool.executables) == {"rscript", "git"}
    assert "git_remote_transfer" in tool.capabilities


def test_argv_uses_the_runtime_only_and_local_features_by_default(tmp_path, runtime, study):
    spec = GitneighbrWebApp().launch(_ctx(tmp_path, runtime), session(tmp_path, study))
    git = runtime.executable("git")
    assert spec.argv[0] == str(runtime.executable("rscript"))
    assert spec.argv[1:3] == ["--vanilla", str(runtime.r_library / "gitneighbr/scripts/serve.R")]
    args = spec.argv[3:]
    assert f"--repo={study}" in args
    assert f"--git={git}" in args
    assert f"--git-exec-path={git.parent.parent / 'libexec' / 'git-core'}" in args
    assert "--features=local" in args
    assert "--base-path=/apps/gitneighbr/" in args
    assert "--port=50002" in args
    assert all(re.match(r"^--[a-z-]+=", a) for a in args)
    # Tokens travel by environment variable name, never on the command line.
    assert not any("t" * 43 in a or spec.env[SHUTDOWN_TOKEN_ENV] in a for a in spec.argv)
    assert spec.auth_headers == {API_TOKEN_HEADER: "t" * 43}
    assert spec.env["R_LIBS_SITE"] == ""
    assert spec.ready_line_prefix == "GITNEIGHBR_READY "


def test_remote_is_offered_only_when_asked(tmp_path, runtime, study):
    spec = GitneighbrWebApp().launch(_ctx(tmp_path, runtime), session(tmp_path, study, remote=True))
    assert "--features=local,remote" in spec.argv


def test_the_policy_file_is_written_outside_the_study(tmp_path, runtime, study):
    ctx = _ctx(tmp_path, runtime)
    spec = GitneighbrWebApp().launch(ctx, session(tmp_path, study))
    policy = Path(next(a.split("=", 1)[1] for a in spec.argv if a.startswith("--policy-file=")))
    assert policy.parent == ctx.workdir
    assert json.loads(policy.read_text()) == history.policy()
    assert not any(study in p.parents for p in [policy])


def test_git_config_inside_the_study_is_refused(tmp_path, runtime, study):
    bad = session(tmp_path, study, git_config=str(study / ".opncafyr" / "gitconfig"))
    with pytest.raises(ValueError, match="outside the workspace"):
        GitneighbrWebApp().launch(_ctx(tmp_path, runtime), bad)
    with pytest.raises(ValueError, match="workspace_root"):
        GitneighbrWebApp().launch(_ctx(tmp_path, runtime), session(tmp_path, tmp_path / "nope"))


def test_a_git_that_finds_its_own_helpers_gets_no_exec_path(tmp_path):
    assert git_exec_path(_exe(tmp_path / "usr" / "bin" / "git")) is None
    win = tmp_path / "win"
    (win / "ucrt64" / "libexec" / "git-core").mkdir(parents=True)
    assert git_exec_path(_exe(win / "cmd" / "git.exe")) == win / "ucrt64" / "libexec" / "git-core"


class _Child:
    def __init__(self, ctx, spec, ready_line=None):
        self.context, self.spec, self.ready_line, self.pid = ctx, spec, ready_line, 1


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_ready_activity_and_two_token_stop(tmp_path, runtime, study):
    app = GitneighbrWebApp()
    ctx = _ctx(tmp_path, runtime)
    spec = app.launch(ctx, session(tmp_path, study))
    seen = []

    def handler(request):
        seen.append(request)
        if request.url.path.endswith("health"):
            return httpx.Response(200, json={"ok": True, "data": {"managed": True, "busy": True}})
        return httpx.Response(200, json={"ok": True})

    line = "GITNEIGHBR_READY " + json.dumps({"url": ctx.base_url})
    with _client(handler) as client:
        assert app.is_ready(_Child(ctx, spec, line), client)
        assert not app.is_ready(_Child(ctx, spec, None), client)
        wrong = "GITNEIGHBR_READY " + json.dumps({"url": "http://127.0.0.1:1/"})
        assert not app.is_ready(_Child(ctx, spec, wrong), client)
        assert isinstance(app, SupportsActivity)
        assert app.activity(_Child(ctx, spec), client) == ChildActivity(unsaved=False, busy=True)
        app.stop(_Child(ctx, spec), client)
    stop = seen[-1]
    assert stop.url.path == "/apps/gitneighbr/api/v1/shutdown"
    assert stop.headers[API_TOKEN_HEADER] == "t" * 43
    assert stop.headers[SHUTDOWN_TOKEN_HEADER] == spec.env[SHUTDOWN_TOKEN_ENV]


def test_policy_protects_the_immutable_records_and_never_ignores_them():
    policy = history.policy()
    protected = policy["protected"]
    records = (".opncafyr/runs/**", ".opncafyr/publications/**")
    records += ("report/builds/**", "deck/builds/**")
    for path in records:
        assert path in protected
    rules = [t["rule"].rstrip("/") for t in policy["ignore_template"]]
    # Never left out: what the user edits, and what they choose about file by file.
    chosen_by_the_user = (".opncafyr/runs", ".opncafyr/publications", "OUTPUTS", "source-data")
    edited = (".opncafyr/recipes/clean.yaml", "report/report.qmd", "report/_quarto.yml")
    edited += ("deck/presentation.yaml", "report/builds/b1", "deck/builds/b1")
    for kept in (*chosen_by_the_user, *edited):
        assert not any(kept == r or kept.startswith(r + "/") for r in rules), kept
        assert not any(r.startswith(kept + "/") for r in rules), kept
    assert all(t["reason"] for t in policy["ignore_template"])
    assert policy["send_blocked"] == []
    # gitneighbr's glob rules: relative, "/", no "..", only * ? ** as wildcards.
    globs = [g for grp in policy["groups"] for g in grp["globs"]] + protected
    globs += [g for r in policy["review"] for g in r["globs"]]
    for glob in globs:
        assert not glob.startswith("/") and "\\" not in glob and ".." not in glob.split("/")
        assert not re.search(r"[\[\]{}!]", glob)
