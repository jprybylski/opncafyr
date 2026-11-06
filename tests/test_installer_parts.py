"""Installer pieces (#19): layout, sources, R relocation, registry, redaction, payload."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from installer_world import PLATFORM, make_world
from opncafyr.core.manifest import validate
from opncafyr.installer import layout as layout_mod
from opncafyr.installer import proc, r_registry
from opncafyr.installer.layout import Layout, installed_root, installed_version_dir
from opncafyr.installer.pins import Pins
from opncafyr.installer.sources import Cache, DownloadSource
from opncafyr.installer.steps.r import contrib_dir, relocate_text_files
from opncafyr.installer.support import redact
from opncafyr.installer.verify import AssetError

ROOT = Path(__file__).resolve().parents[1]


def _complete(layout: Layout, install_id: str) -> None:
    d = layout.version_dir(install_id)
    d.mkdir(parents=True, exist_ok=True)
    (d / "install.json").write_text(json.dumps({"status": "complete"}), "utf-8")


# --- layout ------------------------------------------------------------------------------


def test_app_root_matches_the_launcher(monkeypatch):
    from opncafyr.paths import default_app_root

    monkeypatch.delenv(layout_mod.APP_ROOT_ENV, raising=False)
    assert layout_mod.default_app_root() == default_app_root()


def test_current_points_only_at_complete_installs(tmp_path):
    layout = Layout(tmp_path / "root")
    _complete(layout, "1.0-1")
    layout.version_dir("1.0-2").mkdir(parents=True)
    assert layout.read_current() is None
    layout.write_current("1.0-1")
    assert layout.read_current() == "1.0-1"
    with pytest.raises(ValueError):
        layout.write_current("1.0-2")
    assert layout.read_current() == "1.0-1"
    if sys.platform == "win32":
        assert layout.current.read_text("utf-8").strip() == "1.0-1"
    else:
        assert layout.current.is_symlink()
    # A pointer to an install whose record disappeared counts as none.
    (layout.version_dir("1.0-1") / "install.json").unlink()
    assert layout.read_current() is None


def test_install_ids_count_up(tmp_path):
    layout = Layout(tmp_path / "root", tmp_path / "rroot")
    assert layout.next_install_id("0.1.0") == "0.1.0-1"
    _complete(layout, "0.1.0-1")
    (layout.r_root / "versions" / "0.1.0-4").mkdir(parents=True)  # leftover R folder
    assert layout.next_install_id("0.2.0") == "0.2.0-5"
    with pytest.raises(ValueError):
        layout.next_install_id("../evil")


def test_a_launcher_finds_its_own_install(tmp_path):
    layout = Layout(tmp_path / "root")
    _complete(layout, "1.0-1")
    prefix = layout.version_dir("1.0-1") / "launcher"
    prefix.mkdir()
    assert installed_root(prefix) == layout.root
    assert installed_version_dir(prefix) == layout.version_dir("1.0-1")
    assert installed_root(tmp_path) is None


@pytest.mark.skipif(sys.platform == "win32", reason="current is a pointer file on Windows")
def test_a_launcher_started_through_current_finds_its_own_install(tmp_path):
    layout = Layout(tmp_path / "root")
    _complete(layout, "1.0-1")
    (layout.version_dir("1.0-1") / "launcher").mkdir()
    layout.write_current("1.0-1")
    assert installed_version_dir(layout.current / "launcher") == layout.version_dir("1.0-1")


def test_runtime_record_resolution_order(tmp_path, monkeypatch):
    from opncafyr.core import runtime

    root = tmp_path / "root"
    monkeypatch.setenv(layout_mod.APP_ROOT_ENV, str(root))
    monkeypatch.delenv(runtime.RUNTIME_FILE_ENV, raising=False)
    monkeypatch.setattr(runtime, "active_install_dir", lambda: None)
    assert runtime.default_runtime_file() == root / "runtime" / "runtime.json"
    layout = Layout(root)
    _complete(layout, "1.0-1")
    layout.write_current("1.0-1")
    assert runtime.default_runtime_file() == layout.version_dir("1.0-1") / runtime.RECORD
    own = layout.version_dir("0.9-1")
    monkeypatch.setattr(runtime, "active_install_dir", lambda: own)
    assert runtime.default_runtime_file() == own / runtime.RECORD
    monkeypatch.setenv(runtime.RUNTIME_FILE_ENV, str(tmp_path / "dev.json"))
    assert runtime.default_runtime_file() == tmp_path / "dev.json"


def test_runtime_record_may_name_the_wheelhouse(tmp_path):
    doc = {
        "schema_version": 1,
        "executables": {"python": sys.executable},
        "wheelhouse": str(tmp_path),
    }
    validate(doc, "runtime-v1")


def test_launcher_running_follows_the_instance_lock(tmp_path):
    from opncafyr.core.instance import InstanceLock

    layout = Layout(tmp_path / "root")
    assert not layout.launcher_running()
    lock = InstanceLock(layout.state)
    assert lock.try_acquire()
    try:
        assert layout.launcher_running()
    finally:
        lock.release()
    assert not layout.launcher_running()


# --- pins ---------------------------------------------------------------------------------


def test_real_pins_list_every_file_once_per_platform():
    pins = Pins.load(ROOT / "runtime")
    for platform in ("windows-x86_64", "macos-arm64", "macos-x86_64"):
        assets = pins.assets(platform)
        paths = [a.path for a in assets]
        assert len(paths) == len(set(paths))
        components = {a.component for a in assets}
        assert {"uv", "python", "r", "quarto", "opnca-r", "r-packages", "wheels"} <= components
        assert "libreoffice" not in components
        assert {"libreoffice"} <= {a.component for a in pins.assets(platform, libreoffice=True)}
        assert sum(a.private for a in assets) == 2  # opnca-r manifest and binary
        assert {contrib_dir(a.url) for a in assets if a.component == "r-packages"} == {
            {
                "windows-x86_64": "bin/windows/contrib/4.6",
                "macos-arm64": "bin/macosx/sonoma-arm64/contrib/4.6",
                "macos-x86_64": "bin/macosx/big-sur-x86_64/contrib/4.6",
            }[platform]
        }


def test_wheels_cover_both_environments_and_deckifyr():
    pins = Pins.load(ROOT / "runtime")
    deck = pins.pins["packages"]["deckifyr"]
    for platform in ("windows-x86_64", "macos-arm64", "macos-x86_64"):
        tools = {w["name"]: w for w in pins.wheel_list("tools", platform)}
        assert tools["deckifyr"]["sha256"] == deck["assets"][deck["wheel"]]
        launcher = {w["name"] for w in pins.wheel_list("launcher", platform)}
        assert {"fastapi", "uvicorn", "jsonschema", "psutil", "platformdirs"} <= launcher
        assert not {"pytest", "ruff", "playwright"} & launcher  # no dev tools


# --- downloads ----------------------------------------------------------------------------


def test_private_assets_need_a_token_or_a_local_copy(tmp_path):
    world = make_world(tmp_path)
    asset = next(a for a in Pins.load(world.payload / "runtime").assets(PLATFORM) if a.private)
    source = DownloadSource(Cache(tmp_path / "c"), log=tmp_path / "dl.log", environ={})
    # Rewrite to a GitHub-shaped URL so the private path is taken.
    from dataclasses import replace

    gh = replace(asset, url="https://github.com/o/r/releases/download/v1/" + asset.file)
    with pytest.raises(AssetError, match="GH_TOKEN"):
        source.download(gh)


def test_the_token_goes_to_curl_on_stdin_never_in_argv(tmp_path, monkeypatch):
    world = make_world(tmp_path)
    asset = next(a for a in Pins.load(world.payload / "runtime").assets(PLATFORM) if a.private)
    from dataclasses import replace

    gh = replace(asset, url="https://github.com/o/r/releases/download/v1/" + asset.file)
    calls = []

    def fake_run(argv, *, env, log, stdin=None, **kw):
        calls.append((list(map(str, argv)), stdin, dict(env)))
        out = Path(argv[argv.index("--output") + 1])
        if "api.github.com" in argv[-1]:
            out.write_text(json.dumps({"assets": [{"name": gh.file, "url": "https://api/x"}]}))
        else:
            out.write_bytes(world.files[asset.path])

    monkeypatch.setattr(proc, "run", fake_run)
    token = "ghp_secretsecretsecretsecret00"
    source = DownloadSource(
        Cache(tmp_path / "c"), log=tmp_path / "dl.log", environ={"GH_TOKEN": token}
    )
    path = source.download(gh)
    assert path.read_bytes() == world.files[asset.path]
    for argv, stdin, env in calls:
        assert token not in " ".join(argv)
        assert token not in json.dumps(env)
        assert stdin is not None and token.encode() in stdin
        assert "--proto" in argv and "=https" in argv


def test_children_get_an_allow_listed_environment():
    env = proc.base_env(
        offline=True,
        environ={"HOME": "/h", "PYTHONPATH": "/evil", "R_LIBS": "/evil", "UV_INDEX_URL": "x"},
    )
    assert "PYTHONPATH" not in env and "R_LIBS" not in env and "UV_INDEX_URL" not in env
    assert env["UV_OFFLINE"] == "1" and env["UV_PYTHON_DOWNLOADS"] == "never"
    assert env["R_PROFILE_USER"] == proc.NULL_DEVICE
    assert env["HTTPS_PROXY"] == proc.CLOSED_PROXY  # bundle mode: any attempt fails at once
    traced = proc.base_env(offline=True, environ={proc.PROXY_TRAP_ENV: "0"})
    assert "HTTPS_PROXY" not in traced  # CI: attempts must reach the logged network block
    online = proc.base_env(offline=False, environ={"HTTPS_PROXY": "http://corp:8080"})
    assert online["HTTPS_PROXY"] == "http://corp:8080"


def test_proc_run_needs_an_absolute_executable(tmp_path):
    with pytest.raises(ValueError):
        proc.run(["python", "-V"], env={}, log=tmp_path / "x.log")


# --- R ------------------------------------------------------------------------------------


def test_relocation_edits_text_files_only(tmp_path):
    res = tmp_path / "Resources"
    (res / "bin").mkdir(parents=True)
    (res / "etc").mkdir()
    old = "/Library/Frameworks/R.framework"
    (res / "bin" / "R").write_text(f"R_HOME_DIR={old}/Resources\nexport R_HOME\n", "utf-8")
    (res / "etc" / "Makeconf").write_text(f"LIBR = -F{old}/..\n", "utf-8")
    binary = b"\x00\x01" + old.encode() + b"\x00"
    (res / "bin" / "exec").mkdir()
    (res / "bin" / "exec" / "R").write_bytes(binary)
    changed = relocate_text_files(res, old, "/Users/u/Library/opncafyr/versions/1-1/R.framework")
    assert sorted(p.name for p in changed) == ["Makeconf", "R"]
    assert (res / "bin" / "exec" / "R").read_bytes() == binary
    assert old not in (res / "bin" / "R").read_text("utf-8")


class FakeWinreg:
    HKEY_CURRENT_USER = "HKCU"
    KEY_READ = 1

    def __init__(self):
        self.keys: dict[str, dict[str, tuple[object, int]]] = {}

    class _Handle:
        def __init__(self, reg, key):
            self.reg, self.key = reg, key

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def OpenKey(self, hive, key, reserved=0, access=0):  # noqa: N802 - winreg API
        if key not in self.keys:
            raise FileNotFoundError(key)
        return self._Handle(self, key)

    def EnumValue(self, handle, i):  # noqa: N802
        items = list(self.keys[handle.key].items())
        if i >= len(items):
            raise OSError("no more")
        name, (value, kind) = items[i]
        return (name, value, kind)

    def DeleteKey(self, hive, key):  # noqa: N802
        if key not in self.keys:
            raise FileNotFoundError(key)
        del self.keys[key]

    def CreateKey(self, hive, key):  # noqa: N802
        self.keys.setdefault(key, {})
        return self._Handle(self, key)

    def SetValueEx(self, handle, name, reserved, kind, value):  # noqa: N802
        self.keys[handle.key][name] = (value, kind)


def test_r_installer_never_takes_over_the_users_r_entry():
    reg = FakeWinreg()
    key = r_registry.uninstall_key("4.6.2")
    users = {"DisplayName": ("R for Windows 4.6.2", 1), "InstallLocation": (r"C:\Users\u\R", 1)}
    reg.keys[key] = dict(users)
    saved = r_registry.snapshot(key, reg)
    reg.keys[key] = {"InstallLocation": (r"C:\Users\u\AppData\Local\opncafyr\versions\1\r", 1)}
    r_registry.restore(key, saved, reg)
    assert reg.keys[key] == users
    # No entry before: none after.
    reg2 = FakeWinreg()
    saved2 = r_registry.snapshot(key, reg2)
    reg2.keys[key] = {"InstallLocation": ("x", 1)}
    r_registry.restore(key, saved2, reg2)
    assert key not in reg2.keys


def test_registry_ownership_is_by_install_location():
    reg = FakeWinreg()
    key = r_registry.uninstall_key("4.6.2")
    reg.keys[key] = {"InstallLocation": (r"C:\Users\u\AppData\Local\opncafyr\versions\1\r", 1)}
    assert r_registry.owned_by(key, r"C:\Users\u\AppData\Local\opncafyr", reg)
    assert not r_registry.owned_by(key, r"C:\Other", reg)


# --- support bundle -----------------------------------------------------------------------


def test_redaction_masks_home_user_and_tokens():
    text = (
        "/Users/alice/Library/x ghp_abcdefghijklmnopqrstuvwxyz12 alice\n"
        "Authorization: Bearer abc.def https://h/?token=zzz github_pat_" + "a" * 30 + "\n"
        r"C:\Users\alice\AppData\Local\opncafyr"
    )
    out = redact(text, home="/Users/alice", user="alice")
    assert "alice" not in out
    assert "ghp_" not in out and "github_pat_" not in out and "zzz" not in out
    assert "abc.def" not in out
    assert "~/Library/x" in out


# --- payload and wrappers -----------------------------------------------------------------


def _make_installer():
    spec = importlib.util.spec_from_file_location(
        "make_installer", ROOT / "scripts/make_installer.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_bootstrap_tsv_matches_the_pins():
    pins = tomllib.loads((ROOT / "runtime/pins.toml").read_text("utf-8"))
    rows = [r.split("\t") for r in _make_installer().bootstrap_tsv(pins).splitlines()]
    assert {r[0] for r in rows} == set(pins["platforms"]["supported"])
    for platform, name, sha, url in rows:
        asset = pins["python"]["assets"][platform]
        assert (sha, url) == (asset["sha256"], asset["url"])
        assert name == url.rsplit("/", 1)[1].replace("%2B", "+")
    assert all(
        r[1]
        == next(a.file for a in Pins.load(ROOT / "runtime").assets(r[0]) if a.component == "python")
        for r in rows
    )


def test_payload_manifest_validates_and_run_installer_refuses_a_damaged_payload(tmp_path):
    world = make_world(tmp_path)
    doc = json.loads((world.payload / "payload.json").read_text("utf-8"))
    validate(doc, "payload-v1")
    runner = world.payload / "run_installer.py"
    runner.write_text((ROOT / "installer" / "run_installer.py").read_text("utf-8"), "utf-8")
    (world.payload / "runtime" / "pins.toml").write_text("changed", "utf-8")
    done = subprocess.run(
        [sys.executable, "-I", str(runner), "status"], capture_output=True, text=True
    )
    assert done.returncode == 1
    assert "damaged" in done.stderr


def test_wrappers_ship_in_the_payload():
    names = set(_make_installer().WRAPPERS)
    for name in names:
        assert (ROOT / "installer" / name).is_file()
    sh = (ROOT / "installer" / "install.sh").read_text("utf-8")
    assert "sudo" not in sh.split("set -euo pipefail", 1)[1].replace("with sudo", "")
    assert os.access(ROOT / "installer" / "install.sh", os.X_OK) or sys.platform == "win32"
