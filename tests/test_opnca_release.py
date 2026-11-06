import hashlib
import json

import pytest

from opncafyr.adapters.opnca.release import (
    ReleaseError,
    install_name,
    load_pins,
    main,
    select_asset,
    verify_asset,
    verify_manifest,
)

ASSET = b"binary bytes"
ASSET_SHA = hashlib.sha256(ASSET).hexdigest()


def _binary(platform, arch, name=None, sha=ASSET_SHA):
    return {
        "asset": name or f"opnca_9.9.9_R4.6_{platform}-{arch}.tgz",
        "kind": "binary",
        "platform": platform,
        "arch": arch,
        "r_minor": "4.6",
        "bytes": len(ASSET),
        "sha256": sha,
    }


@pytest.fixture
def release(tmp_path):
    manifest = {
        "schema_version": 1,
        "package": "opnca",
        "version": "9.9.9",
        "source_commit": "abc",
        "assets": [
            {"asset": "x_BUILD-INFO.txt", "kind": "build-info", "sha256": "0" * 64},
            _binary("macos", "arm64"),
            _binary("macos", "x86_64", sha="1" * 64),
            _binary("windows", "x86_64", name="opnca_9.9.9_R4.6_windows-x86_64.zip"),
            {
                "asset": "opnca_9.9.9-vendored.tar.gz",
                "kind": "source-vendored",
                "platform": "source",
                "arch": "any",
                "r_minor": "any",
                "sha256": "2" * 64,
            },
        ],
    }
    mpath = tmp_path / "manifest.json"
    mpath.write_text(json.dumps(manifest))
    msha = hashlib.sha256(mpath.read_bytes()).hexdigest()
    pins = tmp_path / "pins.toml"
    pins.write_text(
        "[packages.opnca-r]\n"
        'version = "9.9.9"\n'
        'source_commit = "abc"\n'
        'r_minor = "4.6"\n'
        f'manifest_sha256 = "{msha}"\n'
        "[packages.opnca-r.assets]\n"
        f'"opnca_9.9.9_R4.6_macos-arm64.tgz" = "{ASSET_SHA}"\n'
        f'"opnca_9.9.9_R4.6_windows-x86_64.zip" = "{ASSET_SHA}"\n'
    )
    asset = tmp_path / "opnca_9.9.9_R4.6_macos-arm64.tgz"
    asset.write_bytes(ASSET)
    return mpath, pins, asset


def test_selects_by_fields(release):
    mpath, pins, _ = release
    manifest = verify_manifest(mpath, load_pins(pins))
    entry = select_asset(manifest, platform="windows", arch="x86_64", r_minor="4.6")
    assert entry["asset"].endswith("windows-x86_64.zip")
    assert install_name(entry, "9.9.9") == "opnca_9.9.9.zip"
    vendored = select_asset(manifest, kind="source-vendored")
    assert install_name(vendored, "9.9.9") == "opnca_9.9.9.tar.gz"


def test_unknown_platform_or_r_minor_has_no_match(release):
    mpath, pins, _ = release
    manifest = verify_manifest(mpath, load_pins(pins))
    with pytest.raises(ReleaseError):
        select_asset(manifest, platform="windows", arch="arm64")
    with pytest.raises(ReleaseError):
        select_asset(manifest, platform="macos", arch="arm64", r_minor="4.5")


def test_tampered_manifest_rejected(release):
    mpath, pins, _ = release
    mpath.write_text(mpath.read_text().replace("abc", "abd"))
    with pytest.raises(ReleaseError, match="SHA-256"):
        verify_manifest(mpath, load_pins(pins))


def test_asset_must_match_manifest_and_pin(release):
    mpath, pins, asset = release
    p = load_pins(pins)
    manifest = verify_manifest(mpath, p)
    entry = select_asset(manifest, platform="macos", arch="arm64", r_minor="4.6")
    verify_asset(asset, entry, p)
    asset.write_bytes(b"binary bytez")
    with pytest.raises(ReleaseError):
        verify_asset(asset, entry, p)
    unpinned = select_asset(manifest, platform="macos", arch="x86_64", r_minor="4.6")
    with pytest.raises(ReleaseError, match="not pinned"):
        verify_asset(asset, unpinned, p)


def test_cli(release, capsys):
    mpath, pins, asset = release
    base = ["--pins", str(pins), "--manifest", str(mpath), "--platform", "macos", "--arch", "arm64"]
    assert main(["select", *base]) == 0
    assert capsys.readouterr().out.strip() == asset.name
    assert main(["verify", *base, "--asset", str(asset)]) == 0
    assert capsys.readouterr().out.strip() == "opnca_9.9.9.tgz"


def test_real_pins_parse():
    from pathlib import Path

    pins = load_pins(Path(__file__).resolve().parents[1] / "runtime" / "pins.toml")
    assert pins["r_minor"] == "4.6"
    assert len(pins["manifest_sha256"]) == 64
