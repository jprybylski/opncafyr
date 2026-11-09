"""Installer v1 (#19): verify, stage, commit, activate, fail safely, repair, uninstall."""

from __future__ import annotations

import json
import shutil
import zipfile
from pathlib import Path

import pytest

from installer_world import PLATFORM, run, write_payload
from opncafyr.core.manifest import validate
from opncafyr.installer import install as install_mod
from opncafyr.installer import payload as payload_mod
from opncafyr.installer.install import FAIL_ENV
from opncafyr.installer.pins import Pins
from opncafyr.installer.sources import Cache, LocalSource, resolve
from opncafyr.installer.verify import InstallError, MissingAssets

# --- install, no-op, update ------------------------------------------------------------


def test_install_from_assets_activates_a_complete_install(world, layout, fakes):
    assert run(layout, world.payload, assets=[world.assets]) == 0
    active = layout.read_current()
    assert active == "0.0.1-1"
    record = layout.record(active)
    validate(record, "install-v1")
    assert record["platform"] == PLATFORM and record["source"] == "online"
    names = {c["name"] for c in record["components"]}
    assert {"uv", "python", "r", "quarto", "opnca-r", "r-packages", "wheels"} <= names
    assert fakes["icons"] == ["0.0.1-1"]
    # The payload is kept for repair and uninstall.
    assert (layout.installer_copy / "payload.json").is_file()
    assert (layout.version_dir(active) / "installer" / "payload.json").is_file()
    # Nothing is left in the staging scratch folder.
    assert not (layout.version_dir(active) / "tmp").exists()


def test_second_run_is_a_no_op(world, layout, fakes):
    assert run(layout, world.payload, assets=[world.assets]) == 0
    messages: list[str] = []
    assert run(layout, world.payload, messages, assets=[world.assets]) == 0
    assert fakes["staged"] == ["0.0.1-1"]
    assert any("already installed" in m for m in messages)


def test_second_run_recreates_a_missing_icon(world, layout, fakes):
    assert run(layout, world.payload, assets=[world.assets]) == 0
    fakes["icons"].clear()
    assert run(layout, world.payload, assets=[world.assets]) == 0
    assert fakes["staged"] == ["0.0.1-1"]
    assert fakes["icons"] == ["0.0.1-1"]


def test_update_keeps_the_previous_install_and_removes_older_ones(tmp_path, world, layout, fakes):
    assert run(layout, world.payload, assets=[world.assets]) == 0
    for version in ("0.0.2", "0.0.3"):
        newer = tmp_path / f"payload-{version}"
        shutil.copytree(world.payload / "runtime", newer / "runtime")
        write_payload(newer, version)
        assert run(layout, newer, assets=[world.assets]) == 0
    assert layout.read_current() == "0.0.3-3"
    assert layout.complete_ids() == ["0.0.2-2", "0.0.3-3"]


def test_reinstall_stages_again(world, layout, fakes):
    assert run(layout, world.payload, assets=[world.assets]) == 0
    assert run(layout, world.payload, assets=[world.assets], reinstall=True) == 0
    assert fakes["staged"] == ["0.0.1-1", "0.0.1-2"]
    assert layout.read_current() == "0.0.1-2"


def test_dry_run_changes_nothing(world, layout, fakes):
    messages: list[str] = []
    assert run(layout, world.payload, messages, assets=[world.assets], dry_run=True) == 0
    assert fakes["staged"] == []
    assert not layout.versions.exists()
    assert any("dry run" in m for m in messages)


# --- verification ----------------------------------------------------------------------


def test_tampered_file_is_refused_naming_the_component(world, layout, fakes):
    assert run(layout, world.payload, assets=[world.assets]) == 0
    (world.bundle / "assets" / "quarto" / "quarto-1.11.9-macos.tar.gz").write_bytes(b"evil")
    for cached in layout.downloads.rglob("quarto-*"):
        cached.unlink()
    newer = world.root / "payload-0.0.2"
    shutil.copytree(world.payload / "runtime", newer / "runtime")
    write_payload(newer, "0.0.2")
    with pytest.raises(MissingAssets) as exc:
        run(layout, newer, from_bundle=world.bundle)
    assert exc.value.components == ["quarto"]
    assert "does not match the pinned SHA-256" in str(exc.value)
    assert layout.read_current() == "0.0.1-1"  # the previous install is untouched
    assert fakes["staged"] == ["0.0.1-1"]


def test_missing_bundle_file_names_the_component(world, layout, fakes):
    (world.bundle / "assets" / "opnca-r" / "opnca_0.3.2_R4.6_macos-arm64.tgz").unlink()
    with pytest.raises(MissingAssets) as exc:
        run(layout, world.payload, from_bundle=world.bundle)
    assert exc.value.components == ["opnca-r"]
    assert layout.read_current() is None
    assert fakes["staged"] == []


def test_bundle_mode_never_downloads(world, layout, fakes, monkeypatch):
    def no_network(*a, **k):
        raise AssertionError("bundle mode created a downloader")

    monkeypatch.setattr(install_mod, "DownloadSource", no_network)
    assert run(layout, world.payload, from_bundle=world.bundle) == 0
    assert layout.record(layout.read_current())["source"] == "bundle"


def test_bundle_zip_is_read_without_unpacking_it(world, layout, fakes):
    archive = world.root / "bundle.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        for path in sorted(world.bundle.rglob("*")):
            if path.is_file():
                zf.write(path, f"opncafyr-bundle/{path.relative_to(world.bundle).as_posix()}")
    assert run(layout, world.payload, from_bundle=archive) == 0


def test_resolve_finds_files_by_hash_and_reports_every_problem(world, tmp_path):
    pins = Pins.load(world.payload / "runtime")
    assets = pins.assets(PLATFORM)
    cache = Cache(tmp_path / "cache")
    found = resolve(assets, cache=cache, local=[LocalSource(world.assets, cache)])
    assert len(found) == len(assets)
    (world.assets / "R6_2.6.1.tgz").write_bytes(b"changed")
    (world.assets / "pyro_0.1.1.tar.gz").unlink()
    with pytest.raises(MissingAssets) as exc:
        resolve(assets, cache=Cache(tmp_path / "c2"), local=[LocalSource(world.assets, cache)])
    assert exc.value.components == ["pyro", "r-packages"]


def test_a_damaged_cache_entry_is_dropped(world, tmp_path):
    pins = Pins.load(world.payload / "runtime")
    asset = next(a for a in pins.assets(PLATFORM) if a.component == "uv")
    cache = Cache(tmp_path / "cache")
    cache.put(asset, world.assets / asset.file)
    assert cache.get(asset) is not None
    cache.path(asset).write_bytes(b"bit rot")
    assert cache.get(asset) is None
    assert not cache.path(asset).exists()


def test_damaged_payload_is_refused(world):
    (world.payload / "runtime" / "pins.toml").write_text("tampered", "utf-8")
    with pytest.raises(InstallError, match="damaged"):
        payload_mod.load(world.payload)


# --- failures leave the previous install active ----------------------------------------


def test_failure_after_staging_keeps_the_previous_install(
    tmp_path, world, layout, fakes, monkeypatch
):
    assert run(layout, world.payload, assets=[world.assets]) == 0
    newer = tmp_path / "payload-0.0.2"
    shutil.copytree(world.payload / "runtime", newer / "runtime")
    write_payload(newer, "0.0.2")
    monkeypatch.setenv(FAIL_ENV, "staging")
    with pytest.raises(InstallError) as exc:
        run(layout, newer, assets=[world.assets])
    assert "Previous install still active: 0.0.1-1" in str(exc.value)
    assert layout.read_current() == "0.0.1-1"
    assert layout.install_ids() == ["0.0.1-1"]  # the incomplete 0.0.2-2 was removed
    bundles = list((layout.logs / "support").glob("install-failure-*.zip"))
    assert len(bundles) == 1
    with zipfile.ZipFile(bundles[0]) as zf:
        names = zf.namelist()
        system = json.loads(zf.read("system.json"))
        stage_log = zf.read("logs/stage.log").decode()
    assert {"system.json", "steps.json", "logs/installer.log"} <= set(names)
    assert system["failed_step"] == "staging"
    assert "ghp_" not in stage_log and "<token>" in stage_log
    assert "environ" not in json.dumps(system).lower()


def test_an_interrupted_install_is_never_activated_and_is_cleaned_up(world, layout, fakes):
    assert run(layout, world.payload, assets=[world.assets]) == 0
    # A killed run leaves a folder without install.json.
    stale = layout.version_dir("0.0.1-7")
    (stale / "runtime").mkdir(parents=True)
    assert layout.incomplete_ids() == ["0.0.1-7"]
    assert layout.read_current() == "0.0.1-1"
    with pytest.raises(ValueError):
        layout.write_current("0.0.1-7")
    messages: list[str] = []
    assert run(layout, world.payload, messages, assets=[world.assets]) == 0
    assert not stale.exists()
    assert any("removing incomplete install 0.0.1-7" in m for m in messages)


def test_repair_reinstalls_a_damaged_install(world, layout, fakes, monkeypatch):
    assert run(layout, world.payload, assets=[world.assets]) == 0
    runtime = json.loads(
        (layout.version_dir("0.0.1-1") / "runtime" / "runtime.json").read_text("utf-8")
    )
    Path(runtime["executables"]["rscript"]).unlink()
    messages: list[str] = []
    assert run(layout, world.payload, messages, mode="repair", assets=[world.assets]) == 0
    assert layout.read_current() == "0.0.1-2"
    assert any("damaged: rscript" in m for m in messages)


def test_repair_of_a_healthy_install_changes_nothing(world, layout, fakes):
    assert run(layout, world.payload, assets=[world.assets]) == 0
    messages: list[str] = []
    assert run(layout, world.payload, messages, mode="repair", assets=[world.assets]) == 0
    assert layout.read_current() == "0.0.1-1"
    assert any("nothing to repair" in m for m in messages)


def test_only_one_installer_at_a_time(world, layout, fakes):
    from opncafyr.installer.layout import InstallerBusy

    with layout.install_lock(), pytest.raises(InstallerBusy):
        run(layout, world.payload, assets=[world.assets])


# --- uninstall --------------------------------------------------------------------------


def test_uninstall_removes_app_files_and_never_a_workspace(tmp_path, world, layout, fakes):
    from opncafyr.installer import uninstall as un

    assert run(layout, world.payload, assets=[world.assets]) == 0
    (layout.r_root / "versions" / "0.0.1-1").mkdir(parents=True)
    workspace = tmp_path / "study"
    (workspace / ".opncafyr").mkdir(parents=True)
    (workspace / ".opncafyr" / "workspace.json").write_text("{}", "utf-8")
    (workspace / "data.csv").write_text("a,b\n1,2\n", "utf-8")
    layout.state.joinpath("recent-workspaces.json").write_text(
        json.dumps({"schema_version": "1.0", "workspaces": [{"path": str(workspace)}]}), "utf-8"
    )
    removed_icons: list[bool] = []
    un.icons.remove = lambda layout, log: removed_icons.append(True)  # type: ignore[assignment]
    un.icons.existing = lambda: []  # type: ignore[assignment]
    assert un.uninstall(layout, force=False, dry_run=False, say=lambda m: None) == 0
    assert not layout.root.exists()
    assert not layout.r_root.exists()
    assert removed_icons == [True]
    assert (workspace / "data.csv").read_text("utf-8") == "a,b\n1,2\n"
    assert (workspace / ".opncafyr" / "workspace.json").is_file()


def test_uninstall_can_leave_the_folder_it_runs_from(world, layout, fakes):
    from opncafyr.installer import uninstall as un

    assert run(layout, world.payload, assets=[world.assets]) == 0
    un.icons.remove = lambda layout, log: None  # type: ignore[assignment]
    un.icons.existing = lambda: []  # type: ignore[assignment]
    leave = layout.installer_copy
    assert un.uninstall(layout, force=False, dry_run=False, say=lambda m: None, leave=leave) == 0
    assert sorted(p.name for p in layout.root.iterdir()) == ["installer"]


def test_uninstall_refuses_while_the_launcher_runs(world, layout, fakes):
    from opncafyr.core.instance import InstanceLock
    from opncafyr.installer import uninstall as un

    assert run(layout, world.payload, assets=[world.assets]) == 0
    lock = InstanceLock(layout.state)
    assert lock.try_acquire()
    try:
        with pytest.raises(InstallError, match="running"):
            un.uninstall(layout, force=False, dry_run=False, say=lambda m: None)
    finally:
        lock.release()
    assert layout.root.exists()


def test_uninstall_refuses_when_a_workspace_is_inside_the_app_root(world, layout, fakes):
    from opncafyr.installer import uninstall as un

    assert run(layout, world.payload, assets=[world.assets]) == 0
    inside = layout.root / "my study"
    inside.mkdir()
    layout.state.joinpath("recent-workspaces.json").write_text(
        json.dumps({"schema_version": "1.0", "workspaces": [{"path": str(inside)}]}), "utf-8"
    )
    with pytest.raises(InstallError, match="inside the app folder"):
        un.uninstall(layout, force=True, dry_run=False, say=lambda m: None)
    assert inside.is_dir()
