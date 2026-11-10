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
    TRUST_BEGIN,
    TRUST_END,
    GitneighbrWebApp,
    git_exec_path,
    trust_study_folder,
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


def _safe_entries(config: Path) -> list[str]:
    """What Git itself reads as safe.directory from this file."""
    import shutil
    import subprocess

    git = shutil.which("git")
    if git is None:
        pytest.skip("git is not installed")
    out = subprocess.run(  # noqa: S603 - fixed argv, test only
        [git, "config", "--file", str(config), "--get-all", "safe.directory"],
        capture_output=True,
        text=True,
        check=False,
    )
    return out.stdout.splitlines()


def test_launch_trusts_exactly_the_selected_study_folder(tmp_path, runtime, study):
    # A mounted study folder in a container is often owned by another user, and Git then
    # refuses it. The managed Git's only global config names this one folder (#62).
    s = session(tmp_path, study)
    GitneighbrWebApp().launch(_ctx(tmp_path, runtime), s)
    config = Path(s["git_config"])
    assert _safe_entries(config) == [study.resolve().as_posix()]
    text = config.read_text()
    assert "*" not in text.replace(TRUST_BEGIN, "").replace(TRUST_END, "")
    assert study not in config.resolve().parents  # still outside the study folder


def test_the_trust_follows_the_open_study_and_keeps_the_persons_identity(tmp_path):
    first, second = tmp_path / "A study", tmp_path / "B study"
    first.mkdir()
    second.mkdir()
    config = tmp_path / "gitconfig"
    config.write_text("[user]\n\tname = Ada\n\temail = ada@example.org\n")
    trust_study_folder(config, first)
    trust_study_folder(config, second)
    trust_study_folder(config, second)  # again: still one entry
    assert _safe_entries(config) == [second.resolve().as_posix()]
    text = config.read_text()
    assert text.startswith("[user]\n\tname = Ada\n\temail = ada@example.org\n")
    assert text.count(TRUST_BEGIN) == 1 and text.count(TRUST_END) == 1
    # Git writing the identity later, as gitneighbr does, doesn't disturb the entry.
    config.write_text(text + "[core]\n\teditor = true\n")
    trust_study_folder(config, first)
    assert _safe_entries(config) == [first.resolve().as_posix()]
    assert "editor = true" in config.read_text()


def test_a_trust_block_cut_short_by_a_hand_edit_doesnt_eat_other_settings(tmp_path):
    study = tmp_path / "s"
    study.mkdir()
    config = tmp_path / "gitconfig"
    config.write_text(f'{TRUST_BEGIN}\n[safe]\n\tdirectory = "/old"\n[user]\n\tname = Ada\n')
    trust_study_folder(config, study)
    assert _safe_entries(config) == [study.resolve().as_posix()]
    assert "name = Ada" in config.read_text()


def test_a_path_with_quotes_or_backslashes_round_trips(tmp_path):
    odd = tmp_path / 'Study "Q" back\\slash'
    try:
        odd.mkdir()
    except OSError:
        pytest.skip("this filesystem can't hold the name")
    config = tmp_path / "gitconfig"
    trust_study_folder(config, odd)
    assert _safe_entries(config) == [odd.resolve().as_posix()]


def test_a_path_with_a_control_character_is_refused(tmp_path):
    odd = tmp_path / "bad\nname"
    try:
        odd.mkdir()
    except OSError:
        pytest.skip("this filesystem can't hold the name")
    with pytest.raises(ValueError, match="control character"):
        trust_study_folder(tmp_path / "gitconfig", odd)


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


def _glob_regex(glob: str) -> re.Pattern[str]:
    """gitneighbr's glob rules: ``dir/`` is a folder and all inside it, ``**`` any depth,
    ``*`` and ``?`` within one path part."""
    if glob.endswith("/"):
        glob += "**"
    out, i = "", 0
    while i < len(glob):
        if glob.startswith("**/", i):
            out, i = out + "(?:.*/)?", i + 3
        elif glob.startswith("**", i):
            out, i = out + ".*", i + 2
        elif glob[i] == "*":
            out, i = out + "[^/]*", i + 1
        elif glob[i] == "?":
            out, i = out + "[^/]", i + 1
        else:
            out, i = out + re.escape(glob[i]), i + 1
    return re.compile(out + "$")


def _group_of(policy, path: str) -> str:
    """The first group whose globs match, as gitneighbr assigns them."""
    for group in policy["groups"]:
        if any(_glob_regex(g).match(path) for g in group["globs"]):
            return group["id"]
    return policy["default_group"]["id"]


def test_policy_puts_each_file_in_the_group_a_user_would_expect():
    policy = history.policy()
    expected = {
        ".opncafyr/runs/20261106T120000Z-ab12/manifest.json": "records",
        ".opncafyr/publications/pub-1/publication.json": "records",
        "OUTPUTS/tables/PARENT_run_summary.csv": "generated",
        "report/builds/b1/report-draft.docx": "generated",
        "deck/builds/b1/deck.pptx": "generated",
        "report/OUTPUTS/figures/PARENT_profile.png": "generated",
        ".opncafyr/recipes/clean.yaml": "sources",
        "report/report.qmd": "sources",
        "deck/presentation.yaml": "sources",
        ".opncafyr/workspace.json": "app",
        ".opncafyr/cleaned/r1-abc/cleaned.csv": "app",
        "source-data/pk.csv": "data",
        "notes.md": "other",
    }
    assert {path: _group_of(policy, path) for path in expected} == expected
    ids = [g["id"] for g in policy["groups"]]
    assert len(ids) == len(set(ids))
