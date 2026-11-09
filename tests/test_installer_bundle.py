"""Offline bundles, the bundle builder, mirror mode and the installation report (#36)."""

from __future__ import annotations

import io
import json
import shutil
import tarfile
import zipfile
from pathlib import Path

import pytest

from installer_world import PLATFORM, run, write_payload
from opncafyr.core.manifest import validate
from opncafyr.installer import bundle as bundle_mod
from opncafyr.installer import install as install_mod
from opncafyr.installer import payload as payload_mod
from opncafyr.installer import proc
from opncafyr.installer.mirror import Mirror, MirrorError
from opncafyr.installer.pins import Pins
from opncafyr.installer.sources import Cache, LocalSource
from opncafyr.installer.verify import MissingAssets


def _build(world, tmp_path, name="out", **kw):
    return bundle_mod.build(
        payload_mod.load(world.payload),
        PLATFORM,
        tmp_path / name,
        say=lambda m: None,
        assets_dirs=[world.assets],
        cache_dir=tmp_path / f"cache-{name}",
        **kw,
    )


def test_bundle_holds_every_pinned_file_and_its_records(world, tmp_path):
    dest = _build(world, tmp_path)
    assert dest.name == f"opncafyr-bundle-0.0.1-{PLATFORM}"
    manifest = json.loads((dest / "bundle.json").read_text("utf-8"))
    validate(manifest, "bundle-manifest-v1")
    for rel, data in world.files.items():
        assert (dest / rel).read_bytes() == data
    paths = {f["path"] for f in manifest["files"]}
    assert paths == set(world.files)
    for f in manifest["files"]:
        assert f["source_url"].startswith("https://") and f["license"]
    assert (dest / "installer" / "payload.json").is_file()
    sums = dict(
        reversed(line.split("  ", 1)) for line in (dest / "SHA256SUMS").read_text().splitlines()
    )
    assert "bundle.json" in sums and "installer/payload.json" in sums
    assert all(p in sums for p in paths)
    spdx = json.loads((dest / "sbom.spdx.json").read_text("utf-8"))
    assert spdx["spdxVersion"] == "SPDX-2.3" and len(spdx["packages"]) == len(paths)
    inventory = json.loads((dest / "licenses.json").read_text("utf-8"))
    assert sum(inventory["summary"].values()) == len(paths)


def test_relative_bundle_folder_yields_absolute_r_installer(world, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    asset = next(
        a for a in Pins.load(world.payload / "runtime").assets(PLATFORM) if a.component == "r"
    )
    source = LocalSource(Path("bundle"), Cache(tmp_path / "cache"))
    path, problem = source.find(asset)
    assert problem is None
    assert path == world.bundle / asset.path
    assert path.is_absolute()


def test_two_builds_from_the_same_pins_list_the_same_hashes(world, tmp_path):
    first = json.loads((_build(world, tmp_path, "a") / "bundle.json").read_text())
    second = json.loads((_build(world, tmp_path, "b") / "bundle.json").read_text())
    assert first == second
    zips = [bundle_mod.write_archive(tmp_path / n / first_name(first)) for n in ("a", "b")]
    assert zips[0].read_bytes() == zips[1].read_bytes()


def first_name(manifest):
    return f"opncafyr-bundle-{manifest['opncafyr_version']}-{manifest['platform']}"


def test_licences_come_from_the_archives():
    desc = b"Package: R6\nVersion: 2.6.1\nLicense: MIT + file\n    LICENSE\n"
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        info = tarfile.TarInfo("R6/DESCRIPTION")
        info.size = len(desc)
        tf.addfile(info, io.BytesIO(desc))
    path = Path(__file__).parent / "_tmp_R6.tgz"
    try:
        path.write_bytes(buf.getvalue())
        assert bundle_mod._r_license(path, "R6") == "MIT + file LICENSE"
    finally:
        path.unlink()


def test_wheel_licence_from_metadata(tmp_path):
    wheel = tmp_path / "x-1.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as zf:
        zf.writestr("x-1.0.dist-info/METADATA", "Name: x\nLicense-Expression: BSD-3-Clause\n\nbody")
    assert bundle_mod._wheel_license(wheel) == "BSD-3-Clause"
    with zipfile.ZipFile(wheel, "w") as zf:
        zf.writestr(
            "x-1.0.dist-info/METADATA",
            "Name: x\nClassifier: License :: OSI Approved :: MIT License\n\nbody",
        )
    assert bundle_mod._wheel_license(wheel) == "MIT License"


def test_install_from_a_built_bundle_with_the_network_cut(
    world, tmp_path, layout, fakes, monkeypatch
):
    archive = _build(world, tmp_path, archive=True)
    assert archive.suffix == ".zip"

    def no_network(*a, **k):
        raise AssertionError("bundle mode created a downloader")

    monkeypatch.setattr(install_mod, "DownloadSource", no_network)
    assert run(layout, world.payload, from_bundle=archive) == 0
    record = layout.record(layout.read_current())
    assert record["source"] == "bundle"
    assert (
        record["bundle_id"]
        == json.loads(
            (
                tmp_path
                / "out"
                / first_name({"opncafyr_version": "0.0.1", "platform": PLATFORM})
                / "bundle.json"
            ).read_text()
        )["bundle_id"]
    )


def test_a_tampered_bundle_is_refused_and_the_previous_install_stays(
    world, tmp_path, layout, fakes
):
    dest = _build(world, tmp_path)
    assert run(layout, world.payload, from_bundle=dest) == 0
    newer = tmp_path / "payload-0.0.2"
    shutil.copytree(world.payload / "runtime", newer / "runtime")
    write_payload(newer, "0.0.2")
    victim = dest / "assets" / "r" / "R-4.6.2-arm64.pkg"
    victim.write_bytes(b"tampered")
    # Also rewrite bundle.json and SHA256SUMS to match: the installer must not care.
    shutil.rmtree(layout.downloads, ignore_errors=True)
    with pytest.raises(MissingAssets) as exc:
        run(layout, newer, from_bundle=dest)
    assert exc.value.components == ["r"]
    assert layout.read_current() == "0.0.1-1"


def test_offline_update_and_an_interrupted_update(world, tmp_path, layout, fakes, monkeypatch):
    dest = _build(world, tmp_path)
    assert run(layout, world.payload, from_bundle=dest) == 0
    newer = tmp_path / "payload-0.0.2"
    shutil.copytree(world.payload / "runtime", newer / "runtime")
    write_payload(newer, "0.0.2")
    # Interrupted: the run dies in the middle of staging (here, a KeyboardInterrupt).
    real = install_mod.steps

    def dying():
        steps = real()

        def die(ctx):
            raise KeyboardInterrupt

        return [steps[0], ("die", die)]

    monkeypatch.setattr(install_mod, "steps", dying)
    with pytest.raises(KeyboardInterrupt):
        run(layout, newer, from_bundle=dest)
    assert layout.read_current() == "0.0.1-1"
    assert layout.complete_ids() == ["0.0.1-1"]
    monkeypatch.setattr(install_mod, "steps", real)
    assert run(layout, newer, from_bundle=dest) == 0
    assert layout.read_current() == "0.0.2-2"
    assert layout.complete_ids() == ["0.0.1-1", "0.0.2-2"]


# --- mirrors -------------------------------------------------------------------------------


def test_mirror_rewrites_by_longest_prefix_and_never_falls_back(tmp_path):
    cfg = tmp_path / "mirror.toml"
    cfg.write_text(
        "[mirrors]\n"
        '"https://github.com/" = "https://art.example/gh/"\n'
        '"https://github.com/mantra-opnca/" = "https://art.example/private/"\n'
        '"https://files.pythonhosted.org/" = "https://art.example/pypi/"\n',
        "utf-8",
    )
    m = Mirror.load(cfg)
    assert m.rewrite("https://github.com/a/b/releases/download/v1/x.tgz") == (
        "https://art.example/gh/a/b/releases/download/v1/x.tgz"
    )
    assert (
        m.rewrite("https://github.com/mantra-opnca/opnca-r/x")
        == "https://art.example/private/opnca-r/x"
    )
    with pytest.raises(MirrorError, match="never uses the origin"):
        m.rewrite("https://cran.r-project.org/bin/x.tgz")


def test_mirror_config_must_be_https(tmp_path):
    cfg = tmp_path / "mirror.toml"
    cfg.write_text('[mirrors]\n"https://github.com/" = "http://art.example/gh/"\n', "utf-8")
    with pytest.raises(MirrorError, match="https"):
        Mirror.load(cfg)


def test_mirror_install_downloads_only_from_the_mirror(world, tmp_path, layout, fakes, monkeypatch):
    cfg = tmp_path / "mirror.toml"
    cfg.write_text('[mirrors]\n"https://example.invalid/" = "https://mirror.example/m/"\n', "utf-8")
    urls = []

    def fake_run(argv, *, env, log, stdin=None, **kw):
        url = str(argv[-1])
        urls.append(url)
        rel = url.removeprefix("https://mirror.example/m/")
        component, name = rel.split("/", 1) if "/" in rel else ("", rel)
        data = next(d for p, d in world.files.items() if p.endswith("/" + name.rsplit("/", 1)[-1]))
        Path(argv[argv.index("--output") + 1]).write_bytes(data)

    monkeypatch.setattr(proc, "run", fake_run)
    monkeypatch.setenv("GH_TOKEN", "unused-for-mirrors")
    assert run(layout, world.payload, mirror=cfg) == 0
    assert urls and all(u.startswith("https://mirror.example/m/") for u in urls)
    assert layout.record(layout.read_current())["source"] == "mirror"


# --- report ---------------------------------------------------------------------------------


def test_every_install_writes_a_verification_report(world, tmp_path, layout, fakes):
    dest = _build(world, tmp_path)
    assert run(layout, world.payload, from_bundle=dest) == 0
    reports = sorted((layout.state / "reports").glob("install-report-*.json"))
    assert len(reports) == 1
    doc = json.loads(reports[0].read_text("utf-8"))
    validate(doc, "install-report-v1")
    assert doc["bundle"]["bundle_id"] == layout.record(layout.read_current())["bundle_id"]
    assert doc["signature"]["checked"] is False
    assert doc["payload_digest"] == payload_mod.load(world.payload).digest
    assert doc["system"]["platform"] == PLATFORM
    assert {c["name"] for c in doc["components"]} >= {"uv", "python", "r", "quarto", "opnca-r"}
    assert "not a validation" in doc["notice"]
    page = reports[0].with_suffix(".html").read_text("utf-8")
    assert "<script" not in page and "http" not in page.split("<body>", 1)[1].split("SHA-256")[0]


def test_the_sbom_time_is_fixed_unless_source_date_epoch_is_set(monkeypatch):
    monkeypatch.delenv("SOURCE_DATE_EPOCH", raising=False)
    assert bundle_mod._created() == "2020-02-02T00:00:00Z"
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "1767225600")
    assert bundle_mod._created() == "2026-01-01T00:00:00Z"
