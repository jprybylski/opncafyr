"""The pinned portable Git for version history (#56): optional, by default, best effort.

A failure to fetch, unpack or start Git never fails the install: it completes without
``git`` in runtime.json, and the install record and report say why. Fake archives only;
nothing is downloaded or run (``git --version`` is answered by a stand-in).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from installer_world import GIT_ARCHIVE, PLATFORM, fake_steps, git_archive, make_world, run
from opncafyr.core.manifest import validate
from opncafyr.installer import bundle as bundle_mod
from opncafyr.installer import install as install_mod
from opncafyr.installer import payload as payload_mod
from opncafyr.installer import proc
from opncafyr.installer.pins import Pins
from opncafyr.installer.sources import DownloadSource
from opncafyr.installer.steps import git as git_step
from opncafyr.installer.steps import runtimes
from opncafyr.installer.verify import AssetError

ROOT = Path(__file__).resolve().parents[1]
PLATFORMS = ("windows-x86_64", "macos-arm64", "macos-x86_64")


@pytest.fixture
def git_world(tmp_path):
    return make_world(tmp_path, git=git_archive())


@pytest.fixture
def with_git_step(fakes, monkeypatch):
    """The fake staging steps plus the real Git step, before the runtime record."""
    steps = fake_steps(fakes["staged"])()
    steps.insert(1, ("git", git_step.install))
    monkeypatch.setattr(install_mod, "steps", lambda: steps)
    return fakes


@pytest.fixture
def git_answers(monkeypatch):
    """Stand in for the unpacked Git: record each argv, answer with ``answer``."""
    calls: list[list[str]] = []
    answer = {"code": 0, "out": b"git version 2.56.0\n"}

    real = proc.capture

    def capture(argv, *, env, **kw):
        if Path(str(argv[0])).name not in ("git", "git.exe"):
            return real(argv, env=env, **kw)  # e.g. getconf for the temp folder
        calls.append([str(a) for a in argv])
        return subprocess.CompletedProcess(argv, answer["code"], answer["out"], b"boom")

    monkeypatch.setattr(proc, "capture", capture)
    return calls, answer


def _runtime(layout) -> dict:
    path = layout.version_dir(layout.read_current()) / "runtime" / "runtime.json"
    return json.loads(path.read_text("utf-8"))


def _report(layout) -> dict:
    (path,) = (layout.state / "reports").glob("install-report-*.json")
    return json.loads(path.read_text("utf-8"))


# --- pins ---------------------------------------------------------------------------------


@pytest.mark.parametrize("platform", PLATFORMS)
def test_real_pins_have_git_for_every_platform_by_default(platform):
    pins = Pins.load(ROOT / "runtime")
    section = pins.pins["optional"]["git"]
    assert section["status"] == "optional" and section["default"] is True
    (asset,) = [a for a in pins.assets(platform) if a.component == "git"]
    assert asset.optional and asset.best_effort and not asset.private
    assert asset.version == section["version"] == "2.56.0"
    assert asset.license == "GPL-2.0-only AND MIT"
    assert asset.url.startswith(
        f"https://github.com/desktop/dugite-native/releases/download/{section['release']}/"
    )
    assert asset.file.endswith(".tar.gz") and "2.56.0" in asset.file
    assert {"macos-arm64": "macOS-arm64", "macos-x86_64": "macOS-x64"}.get(
        platform, "windows-x64"
    ) in asset.file
    assert len(asset.sha256) == 64
    assert "git" not in {a.component for a in pins.assets(platform, git=False)}
    # LibreOffice is optional too, but only on request, and then it must succeed.
    lo = next(a for a in pins.assets(platform, libreoffice=True) if a.component == "libreoffice")
    assert lo.optional and not lo.best_effort


def test_exec_path_follows_the_executable():
    mac = Path("/a b/versions/1/git/bin/git")
    win = Path("C:/x/versions/1/git/cmd/git.exe")
    assert git_step.exec_path(mac) == Path("/a b/versions/1/git/libexec/git-core")
    assert git_step.exec_path(win) == Path("C:/x/versions/1/git/ucrt64/libexec/git-core")
    assert git_step.layout_for("windows-x86_64") == ("cmd/git.exe", "ucrt64/libexec/git-core")
    assert git_step.layout_for("macos-arm64") == ("bin/git", "libexec/git-core")


# --- install ------------------------------------------------------------------------------


def test_git_is_installed_by_default_and_recorded(git_world, layout, with_git_step, git_answers):
    calls, _ = git_answers
    assert run(layout, git_world.payload, assets=[git_world.assets]) == 0
    vdir = layout.version_dir(layout.read_current())
    git = vdir / "git" / "bin" / "git"
    assert _runtime(layout)["executables"]["git"] == str(git)
    assert git.is_file()
    if sys.platform != "win32":
        assert os.access(git, os.X_OK)  # the archive's exec bits survive unpacking
    assert calls == [
        [str(git), f"--exec-path={vdir / 'git' / 'libexec' / 'git-core'}", "--version"]
    ]
    record = layout.record(layout.read_current())
    validate(record, "install-v1")
    assert record["optional"] == [
        {"name": "git", "status": "installed", "detail": "git version 2.56.0"}
    ]
    git_component = next(c for c in record["components"] if c["name"] == "git")
    assert git_component["version"] == "2.56.0"
    assert git_component["files"][0]["file"] == GIT_ARCHIVE
    report = _report(layout)
    validate(report, "install-report-v1")
    assert report["optional"][0]["status"] == "installed" and report["result"] == "pass"


@pytest.mark.parametrize(
    "case",
    ["does not start", "wrong version", "no executable", "no exec path", "not an archive"],
)
def test_a_failing_git_leaves_a_working_install_without_it(
    tmp_path, layout, with_git_step, git_answers, case
):
    _, answer = git_answers
    archive = git_archive()
    if case == "does not start":
        answer["code"] = 1
    elif case == "wrong version":
        answer["out"] = b"git version 2.55.1\n"
    elif case == "no executable":
        archive = git_archive({"libexec/git-core/": None})
    elif case == "no exec path":
        archive = git_archive({"bin/git": b"#!/bin/sh\n"})
    else:
        archive = b"not a tar.gz"
    world = make_world(tmp_path / "w", git=archive)
    messages: list[str] = []
    assert run(layout, world.payload, messages, assets=[world.assets]) == 0
    active = layout.read_current()
    assert active is not None
    assert "git" not in _runtime(layout)["executables"]
    assert not (layout.version_dir(active) / "git").exists()  # no half-unpacked folder
    record = layout.record(active)
    validate(record, "install-v1")
    (outcome,) = record["optional"]
    assert outcome["name"] == "git" and outcome["status"] == "failed"
    assert {
        "does not start": "Git reports",
        "wrong version": "2.55.1",
        "no executable": "has no bin/git",
        "no exec path": "has no libexec/git-core",
        "not an archive": "ReadError",
    }[case] in outcome["detail"]
    assert "git" not in {c["name"] for c in record["components"]}
    assert {"uv", "python", "r", "quarto", "opnca-r"} <= {c["name"] for c in record["components"]}
    assert any("version history is unavailable" in m for m in messages)
    assert any("--reinstall" in m for m in messages)
    report = _report(layout)
    validate(report, "install-report-v1")
    assert report["result"] == "pass" and report["optional"][0]["status"] == "failed"


def test_a_bundle_without_git_still_installs(git_world, layout, with_git_step, git_answers):
    (git_world.bundle / "assets" / "git" / GIT_ARCHIVE).unlink()
    messages: list[str] = []
    assert run(layout, git_world.payload, messages, from_bundle=git_world.bundle) == 0
    record = layout.record(layout.read_current())
    validate(record, "install-v1")
    (outcome,) = record["optional"]
    assert outcome["status"] == "failed" and "not fetched" in outcome["detail"]
    assert "git" not in _runtime(layout)["executables"]
    assert any("optional git could not be fetched" in m for m in messages)
    assert git_answers[0] == []  # nothing to start


def test_a_damaged_git_file_is_never_used(git_world, layout, with_git_step, git_answers):
    (git_world.bundle / "assets" / "git" / GIT_ARCHIVE).write_bytes(b"evil")
    assert run(layout, git_world.payload, from_bundle=git_world.bundle) == 0
    (outcome,) = layout.record(layout.read_current())["optional"]
    assert outcome["status"] == "failed" and "SHA-256" in outcome["detail"]
    assert "git" not in _runtime(layout)["executables"]


def test_required_files_still_fail_the_install(git_world, layout, with_git_step, git_answers):
    from opncafyr.installer.verify import MissingAssets

    (git_world.bundle / "assets" / "git" / GIT_ARCHIVE).unlink()
    (git_world.bundle / "assets" / "quarto" / "quarto-1.11.9-macos.tar.gz").unlink()
    with pytest.raises(MissingAssets) as exc:
        run(layout, git_world.payload, from_bundle=git_world.bundle)
    assert exc.value.components == ["quarto"]
    assert layout.read_current() is None


def test_without_git_skips_it(git_world, layout, with_git_step, git_answers):
    assert run(layout, git_world.payload, assets=[git_world.assets], with_git=False) == 0
    record = layout.record(layout.read_current())
    validate(record, "install-v1")
    assert record["optional"] == [
        {"name": "git", "status": "skipped", "detail": "not requested (--without-git)"}
    ]
    assert "git" not in _runtime(layout)["executables"]
    assert git_answers[0] == []
    payload = payload_mod.load(git_world.payload)
    on = install_mod.identity(payload, PLATFORM, install_mod.Options())
    off = install_mod.identity(payload, PLATFORM, install_mod.Options(with_git=False))
    assert on != off  # a later default install adds Git instead of being a no-op


def test_the_installer_cli_has_without_git():
    from opncafyr.installer import cli

    for argv in (["install", "--without-git"], ["bundle", "--out", "x", "--without-git"]):
        assert cli.build_parser().parse_args(argv).without_git is True
    assert cli.build_parser().parse_args(["install"]).without_git is False


def test_runtime_record_names_git_only_when_installed(tmp_path, world, layout):
    from opncafyr.installer.steps import Context, venv_python

    def ctx_with(executables):
        ctx = Context(
            layout=layout,
            platform=PLATFORM,
            install_id="0.0.1-1",
            pins=Pins.load(world.payload / "runtime"),
            payload=payload_mod.load(world.payload),
            assets=[],
            files={},
            log_dir=tmp_path / "logs",
            offline=True,
            say=lambda m: None,
        )
        for venv in ("launcher", "tools"):
            exe = venv_python(ctx.dir / venv)
            exe.parent.mkdir(parents=True, exist_ok=True)
            exe.write_text("", "utf-8")
        for name in ("uv", "rscript", "quarto", *executables):
            path = ctx.dir / "bin" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("", "utf-8")
            ctx.executables[name] = path
        (ctx.dir / "lib").mkdir(exist_ok=True)
        ctx.paths |= {"r_library": ctx.dir / "lib", "wheelhouse": ctx.dir / "lib"}
        return ctx

    ctx = ctx_with([])
    runtimes.runtime_record(ctx)
    doc = json.loads((ctx.dir / "runtime" / "runtime.json").read_text("utf-8"))
    assert "git" not in doc["executables"]
    ctx = ctx_with(["git"])
    runtimes.runtime_record(ctx)
    doc = json.loads((ctx.dir / "runtime" / "runtime.json").read_text("utf-8"))
    validate(doc, "runtime-v1")
    assert doc["executables"]["git"] == str(ctx.dir / "bin" / "git")


# --- bundles ------------------------------------------------------------------------------


def _build(world, tmp_path, name="out", **kw):
    return bundle_mod.build(
        payload_mod.load(world.payload),
        PLATFORM,
        tmp_path / name,
        say=kw.pop("say", lambda m: None),
        assets_dirs=[world.assets],
        cache_dir=tmp_path / f"cache-{name}",
        **kw,
    )


def test_bundles_carry_git_by_default(git_world, tmp_path):
    dest = _build(git_world, tmp_path)
    manifest = json.loads((dest / "bundle.json").read_text("utf-8"))
    validate(manifest, "bundle-manifest-v1")
    (entry,) = [f for f in manifest["files"] if f["component"] == "git"]
    assert entry["path"] == f"assets/git/{GIT_ARCHIVE}" and entry["optional"] is True
    assert entry["license"] == "GPL-2.0-only AND MIT"
    assert (dest / entry["path"]).read_bytes() == git_world.files[entry["path"]]
    without = _build(git_world, tmp_path, "without", with_git=False)
    files = json.loads((without / "bundle.json").read_text("utf-8"))["files"]
    assert "git" not in {f["component"] for f in files}


def test_a_bundle_is_built_without_git_when_it_cannot_be_fetched(git_world, tmp_path, monkeypatch):
    (git_world.assets / GIT_ARCHIVE).unlink()

    def offline(self, asset):
        raise AssetError(asset.component, asset.file, "download failed: no route")

    monkeypatch.setattr(DownloadSource, "download", offline)
    messages: list[str] = []
    dest = _build(git_world, tmp_path, say=messages.append)
    manifest = json.loads((dest / "bundle.json").read_text("utf-8"))
    assert "git" not in {f["component"] for f in manifest["files"]}
    assert not (dest / "assets" / "git").exists()
    assert any("leaving out the optional git" in m for m in messages)


# --- doctor -------------------------------------------------------------------------------


class _FakeProcess:
    def __init__(self, out: bytes, code: int = 0) -> None:
        self.popen = self
        self.returncode = code
        self._out = out

    def communicate(self, timeout=None):
        return self._out, None

    def kill_tree(self):
        pass


@pytest.fixture
def doctor_env(tmp_path, monkeypatch):
    monkeypatch.setenv("OPNCAFYR_APP_ROOT", str(tmp_path / "app root"))
    runtime_file = tmp_path / "runtime.json"
    monkeypatch.setenv("OPNCAFYR_RUNTIME_FILE", str(runtime_file))
    # A git on PATH is never the trusted one.
    monkeypatch.setattr("shutil.which", lambda name, *a, **k: f"/usr/bin/{name}")
    return runtime_file


def _git_line(out: str) -> str:
    return next(line for line in out.splitlines() if line.startswith("git "))


def test_doctor_reports_the_pinned_git(tmp_path, doctor_env, monkeypatch, capsys):
    from opncafyr import cli

    git = tmp_path / "install" / "git" / "bin" / "git"
    git.parent.mkdir(parents=True)
    git.write_text("", "utf-8")
    doctor_env.write_text(
        json.dumps({"schema_version": 1, "executables": {"git": str(git)}}), "utf-8"
    )
    seen = []

    def spawn(argv, *, env, cwd, stdout=None, stderr=None):
        seen.append([str(a) for a in argv])
        return _FakeProcess(b"git version 2.56.0\n")

    monkeypatch.setattr("opncafyr.core.proctree.spawn", spawn)
    assert cli.main(["doctor"]) == 0
    line = _git_line(capsys.readouterr().out)
    assert str(git) in line and "git version 2.56.0" in line and "/usr/bin/git" not in line
    exec_path = tmp_path / "install" / "git" / "libexec" / "git-core"
    assert [str(git), f"--exec-path={exec_path}", "--version"] in seen


def test_doctor_says_when_git_is_not_installed(doctor_env, capsys):
    from opncafyr import cli

    doctor_env.write_text(json.dumps({"schema_version": 1, "executables": {}}), "utf-8")
    assert cli.main(["doctor"]) == 0
    line = _git_line(capsys.readouterr().out)
    assert line.split(None, 1)[1] == "not installed (version history unavailable)"
