"""The pinned datum binary for external data (#83): optional, by default, best effort.

Like Git (#56), a failure to fetch, unpack or start datum never fails the install: it
completes without ``datum`` in runtime.json, and the install record and report say why.
Fake archives only; ``datum --version`` is answered by a stand-in.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from installer_world import DATUM_ARCHIVE, PLATFORM, datum_archive, fake_steps, make_world, run
from opncafyr.core.manifest import validate
from opncafyr.installer import install as install_mod
from opncafyr.installer import payload as payload_mod
from opncafyr.installer import proc
from opncafyr.installer.pins import Pins
from opncafyr.installer.steps import datum as datum_step

ROOT = Path(__file__).resolve().parents[1]
PLATFORMS = ("windows-x86_64", "macos-arm64", "macos-x86_64", "linux-x86_64", "linux-aarch64")
ASSET_NAME = {
    "windows-x86_64": "datum_1.6.0_windows_amd64.zip",
    "macos-arm64": "datum_1.6.0_darwin_arm64.tar.gz",
    "macos-x86_64": "datum_1.6.0_darwin_amd64.tar.gz",
    "linux-x86_64": "datum_1.6.0_linux_amd64.tar.gz",
    "linux-aarch64": "datum_1.6.0_linux_arm64.tar.gz",
}


@pytest.fixture
def datum_world(tmp_path):
    return make_world(tmp_path, datum=datum_archive())


@pytest.fixture
def with_datum_step(fakes, monkeypatch):
    steps = fake_steps(fakes["staged"])()
    steps.insert(1, ("datum", datum_step.install))
    monkeypatch.setattr(install_mod, "steps", lambda: steps)
    return fakes


@pytest.fixture
def datum_answers(monkeypatch):
    calls: list[list[str]] = []
    answer = {"code": 0, "out": b"datum v1.6.0\n"}
    real = proc.capture

    def capture(argv, *, env, **kw):
        if Path(str(argv[0])).name not in ("datum", "datum.exe"):
            return real(argv, env=env, **kw)
        calls.append([str(a) for a in argv])
        return subprocess.CompletedProcess(argv, answer["code"], answer["out"], b"boom")

    monkeypatch.setattr(proc, "capture", capture)
    return calls, answer


def _runtime(layout) -> dict:
    path = layout.version_dir(layout.read_current()) / "runtime" / "runtime.json"
    return json.loads(path.read_text("utf-8"))


@pytest.mark.parametrize("platform", PLATFORMS)
def test_real_pins_have_datum_for_every_platform_by_default(platform):
    pins = Pins.load(ROOT / "runtime")
    section = pins.pins["optional"]["datum"]
    assert section["status"] == "optional" and section["default"] is True
    assert section["license"] == "MIT" and section["version"] == "1.6.0"
    (asset,) = [a for a in pins.assets(platform, allow_pending=True) if a.component == "datum"]
    assert asset.optional and asset.best_effort
    assert asset.url == (
        f"https://github.com/jprybylski/datum/releases/download/v1.6.0/{ASSET_NAME[platform]}"
    )
    assert len(asset.sha256) == 64
    names = {a.component for a in pins.assets(platform, datum=False, allow_pending=True)}
    assert "datum" not in names


def test_datum_is_installed_by_default_and_recorded(
    datum_world, layout, with_datum_step, datum_answers
):
    calls, _ = datum_answers
    assert run(layout, datum_world.payload, assets=[datum_world.assets]) == 0
    exe = layout.version_dir(layout.read_current()) / "datum" / "datum"
    assert _runtime(layout)["executables"]["datum"] == str(exe)
    if sys.platform != "win32":
        assert os.access(exe, os.X_OK)
    assert calls == [[str(exe), "--version"]]
    record = layout.record(layout.read_current())
    validate(record, "install-v1")
    assert record["optional"] == [
        {"name": "datum", "status": "installed", "detail": "datum v1.6.0"}
    ]
    component = next(c for c in record["components"] if c["name"] == "datum")
    assert component["version"] == "1.6.0"
    assert component["files"][0]["file"] == DATUM_ARCHIVE


@pytest.mark.parametrize("case", ["does not start", "wrong version", "no binary", "not an archive"])
def test_a_failing_datum_leaves_a_working_install_without_it(
    tmp_path, layout, with_datum_step, datum_answers, case
):
    _, answer = datum_answers
    archive = datum_archive()
    if case == "does not start":
        answer["code"] = 1
    elif case == "wrong version":
        answer["out"] = b"datum v1.5.0\n"
    elif case == "no binary":
        archive = datum_archive({"LICENSE": b"MIT\n"})
    else:
        archive = b"not a tar.gz"
    world = make_world(tmp_path / "w", datum=archive)
    messages: list[str] = []
    assert run(layout, world.payload, messages, assets=[world.assets]) == 0
    active = layout.read_current()
    assert "datum" not in _runtime(layout)["executables"]
    assert not (layout.version_dir(active) / "datum").exists()
    record = layout.record(active)
    validate(record, "install-v1")
    (outcome,) = record["optional"]
    assert outcome["name"] == "datum" and outcome["status"] == "failed"
    assert {
        "does not start": "datum reports",
        "wrong version": "1.5.0",
        "no binary": "has no datum",
        "not an archive": "ReadError",
    }[case] in outcome["detail"]
    assert any("External data page is unavailable" in m for m in messages)


def test_a_bundle_without_datum_still_installs(datum_world, layout, with_datum_step, datum_answers):
    (datum_world.bundle / "assets" / "datum" / DATUM_ARCHIVE).unlink()
    assert run(layout, datum_world.payload, from_bundle=datum_world.bundle) == 0
    (outcome,) = layout.record(layout.read_current())["optional"]
    assert outcome["status"] == "failed" and "not fetched" in outcome["detail"]
    assert "datum" not in _runtime(layout)["executables"]
    assert datum_answers[0] == []


def test_without_datum_skips_it(datum_world, layout, with_datum_step, datum_answers):
    assert run(layout, datum_world.payload, assets=[datum_world.assets], with_datum=False) == 0
    record = layout.record(layout.read_current())
    assert record["optional"] == [
        {"name": "datum", "status": "skipped", "detail": "not requested (--without-datum)"}
    ]
    assert "datum" not in _runtime(layout)["executables"]
    payload = payload_mod.load(datum_world.payload)
    on = install_mod.identity(payload, PLATFORM, install_mod.Options())
    off = install_mod.identity(payload, PLATFORM, install_mod.Options(with_datum=False))
    assert on != off


def test_the_installer_cli_has_without_datum():
    from opncafyr.installer import cli

    for argv in (["install", "--without-datum"], ["bundle", "--out", "x", "--without-datum"]):
        assert cli.build_parser().parse_args(argv).without_datum is True
    assert cli.build_parser().parse_args(["install"]).without_datum is False


def test_binary_name_per_platform():
    assert datum_step.binary_for("windows-x86_64") == "datum.exe"
    assert datum_step.binary_for("linux-aarch64") == "datum"
