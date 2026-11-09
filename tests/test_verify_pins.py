"""scripts/verify_pins.py structure checks (no network)."""

import importlib.util
import shutil
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("verify_pins", ROOT / "scripts" / "verify_pins.py")
verify_pins = importlib.util.module_from_spec(_spec)
sys.modules["verify_pins"] = verify_pins
_spec.loader.exec_module(verify_pins)


@pytest.fixture
def tree(tmp_path):
    """A copy of the pins and every lock they point to."""
    for rel in (
        "runtime/pins.toml",
        "runtime/r/renv.lock",
        "runtime/r/binaries.toml",
        "runtime/python/tools/uv.lock",
        "runtime/python/wheels.toml",
        "uv.lock",
    ):
        dst = tmp_path / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / rel, dst)
    return tmp_path


def _check(root):
    pins = tomllib.loads((root / "runtime/pins.toml").read_text("utf-8"))
    return verify_pins.check_structure(pins, root)


def _edit(root, rel, old, new):
    path = root / rel
    text = path.read_text("utf-8")
    assert old in text, f"{old!r} not in {rel}"
    path.write_text(text.replace(old, new, 1), "utf-8")


def test_committed_pins_pass():
    errors, assets = _check(ROOT)
    assert errors == []
    sections = {a.section.split(".")[0] for a in assets}
    assert {"uv", "python", "r", "quarto", "packages", "r_packages", "rust", "rtools"} <= sections


def test_every_supported_platform_has_runtimes():
    pins = tomllib.loads((ROOT / "runtime/pins.toml").read_text("utf-8"))
    for name in ("uv", "python", "r", "quarto"):
        assert set(pins[name]["assets"]) == set(pins["platforms"]["supported"])


def test_tbd_is_rejected(tree):
    _edit(tree, "runtime/pins.toml", 'version = "1.11.9"', 'version = "TBD"')
    errors, _ = _check(tree)
    assert any("quarto.version is still TBD" in e for e in errors)


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/astral-sh/uv/releases/latest/download/uv-aarch64-apple-darwin.tar.gz",
        "https://github.com/x/y/archive/refs/heads/main/x.tar.gz",
        "https://a2-ai.r-universe.dev/src/contrib/reportifyr_0.4.0.tar.gz",
    ],
)
def test_moving_refs_are_rejected(tree, url):
    _edit(
        tree,
        "runtime/pins.toml",
        "https://github.com/astral-sh/uv/releases/download/0.13.7/uv-aarch64-apple-darwin.tar.gz",
        url,
    )
    errors, _ = _check(tree)
    assert any("moving ref" in e for e in errors)


def test_missing_platform_asset_is_rejected(tree):
    _edit(tree, "runtime/pins.toml", "[quarto.assets.macos-x86_64]", "[quarto.assets.linux-x86_64]")
    errors, _ = _check(tree)
    assert "[quarto] has no asset for macos-x86_64" in errors


def test_malformed_sha_is_rejected(tree):
    _edit(
        tree,
        "runtime/pins.toml",
        "81b811b57e4624df7ed3aac148e0359100d9962f3d9dce00aec533dc2e086731",
        "81b811b5",
    )
    errors, _ = _check(tree)
    assert "[uv.assets.macos-arm64] sha256 missing or malformed" in errors


def test_opnca_needs_a_binary_per_platform(tree):
    _edit(tree, "runtime/pins.toml", '"opnca_0.3.1_R4.6_windows-x86_64.zip" = ', '"x.zip" = ')
    errors, _ = _check(tree)
    assert "[packages.opnca-r] has no binary for windows-x86_64" in errors


def test_binaries_must_match_the_lock(tree):
    _edit(tree, "runtime/r/binaries.toml", 'version = "1.30.2-1"', 'version = "1.30.2-0"')
    errors, _ = _check(tree)
    assert any("AsioHeaders" in e for e in errors)


def test_lock_package_without_binaries_is_rejected(tree):
    path = tree / "runtime/r/binaries.toml"
    text = path.read_text("utf-8")
    start = text.index('[packages."R6".windows-x86_64]')
    end = text.index("[packages.", start + 1)
    path.write_text(text[:start] + text[end:], "utf-8")
    errors, _ = _check(tree)
    assert "binaries.toml has no windows-x86_64 file for R6" in errors


def test_cellar_version_must_match_the_pin(tree):
    _edit(tree, "runtime/pins.toml", 'version = "0.4.0"', 'version = "0.4.1"')
    errors, _ = _check(tree)
    assert any("reportifyr 0.4.0 differs from the pin" in e for e in errors)


def test_tools_lock_must_pin_the_deckifyr_wheel_hash(tree):
    _edit(
        tree,
        "runtime/python/tools/uv.lock",
        "sha256:25b150a834249c2d63bd86b629ef92715be4aafa98d029bf1c9bae7328e82328",
        "sha256:" + "0" * 64,
    )
    errors, _ = _check(tree)
    assert any("does not pin deckifyr-0.3.6-py3-none-any.whl" in e for e in errors)


def test_cran_binaries_only_with_origin(tree):
    _edit(tree, "runtime/r/binaries.toml", 'origin = "cran"\n', "")
    errors, _ = _check(tree)
    assert any("gdtools macos-arm64 has a bad url" in e for e in errors)


def test_renv_pin_must_match_the_lock(tree):
    _edit(tree, "runtime/pins.toml", 'renv = "1.3.1"', 'renv = "1.2.3"')
    errors, _ = _check(tree)
    assert any("renv.lock has renv 1.3.1" in e for e in errors)


def test_tool_packages_must_come_from_the_cellar(tree):
    _edit(tree, "runtime/r/renv.lock", '"Source": "Cellar"', '"Source": "GitHub"')
    errors, _ = _check(tree)
    assert any("must be Source: Cellar" in e for e in errors)


def test_every_section_needs_a_license(tree):
    _edit(tree, "runtime/pins.toml", 'license = "MIT OR Apache-2.0"\n', "")
    errors, _ = _check(tree)
    assert "[uv] has no license" in errors
    _edit(tree, "runtime/pins.toml", 'license = "Apache-2.0"\n', "")
    errors, _ = _check(tree)
    assert "[packages.opnca-r] has no license" in errors


def test_wheels_toml_must_pin_the_deckifyr_wheel(tree):
    _edit(tree, "runtime/python/wheels.toml", "25b150a834249c2d", "0000000000000000")
    errors, _ = _check(tree)
    assert any("does not pin the deckifyr wheel" in e for e in errors)


def test_wheels_toml_is_current():
    assert verify_pins._check_wheels_current() == []


def test_git_is_pinned_and_hashed_for_every_platform():
    errors, assets = _check(ROOT)
    assert errors == []
    pins = tomllib.loads((ROOT / "runtime/pins.toml").read_text("utf-8"))
    # --network hashes every asset in this list, Git's too (#56).
    hashed = {a.label for a in assets if a.section == "optional.git"}
    assert hashed == set(pins["platforms"]["supported"])


def test_optional_git_must_stay_on_its_release(tree):
    _edit(
        tree,
        "runtime/pins.toml",
        "releases/download/v2.56.0-2/dugite-native-v2.56.0-a44aeca-macOS-x64",
        "releases/download/v2.55.0-1/dugite-native-v2.56.0-a44aeca-macOS-x64",
    )
    errors, _ = _check(tree)
    assert "[optional.git.assets.macos-x86_64] is not release v2.56.0-2, 2.56.0" in errors


def test_optional_default_must_be_a_boolean(tree):
    _edit(tree, "runtime/pins.toml", "default = true\n", 'default = "yes"\n')
    errors, _ = _check(tree)
    assert "[optional.git] default must be true or false" in errors


def test_optional_git_needs_every_platform(tree):
    _edit(
        tree,
        "runtime/pins.toml",
        "[optional.git.assets.windows-x86_64]",
        "[optional.git.assets.windows-arm64]",
    )
    errors, _ = _check(tree)
    assert "[optional.git] has no asset for windows-x86_64" in errors


def test_both_linux_platforms_pin_the_same_runtimes():
    pins = tomllib.loads((ROOT / "runtime/pins.toml").read_text("utf-8"))
    for platform in ("linux-x86_64", "linux-aarch64"):
        assert set(pins["ci"][platform]) == {"uv", "python", "quarto", "r", "rust"}


def test_container_base_image_must_be_pinned_by_digest(tree):
    _edit(
        tree,
        "runtime/pins.toml",
        'base_image = "docker.io/library/ubuntu:24.04@sha256:',
        'base_image = "docker.io/library/ubuntu:24.04" # @sha256:',
    )
    errors, _ = _check(tree)
    assert any("base_image must be pinned by digest" in e for e in errors)


def test_apt_locks_must_name_exact_versions(tree):
    locks = tree / "container" / "apt"
    locks.mkdir(parents=True)
    for platform in ("linux-x86_64", "linux-aarch64"):
        for stage in ("runtime", "build", "full"):
            (locks / f"lock-{platform}-{stage}.txt").write_text(
                "# header\nzlib1g=1:1.3.dfsg-3.1ubuntu2.1\n"
            )
    assert _check(tree)[0] == []
    (locks / "lock-linux-aarch64-full.txt").write_text("libreoffice\n")
    errors, _ = _check(tree)
    assert any("lock-linux-aarch64-full.txt:1 is not package=version" in e for e in errors)
    (locks / "lock-linux-x86_64-build.txt").unlink()
    errors, _ = _check(tree)
    assert any("lock-linux-x86_64-build.txt is missing" in e for e in errors)


def test_a_linux_platform_missing_a_runtime_is_reported(tree):
    _edit(tree, "runtime/pins.toml", "[ci.linux-aarch64.rust]", "[ci.linux-aarch64.rustc]")
    errors, _ = _check(tree)
    assert any("[ci.linux-aarch64] must pin exactly" in e for e in errors)
