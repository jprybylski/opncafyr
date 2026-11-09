"""#34: opnca-r's binary install never touches the user's R library, and the source
fallback (`repair --build-from-source`) is wired, pinned and safe.

The real build runs in CI (`.github/workflows/installer.yml`, job `source-build`). These
tests cover the parts that need no compiler.
"""

from __future__ import annotations

import io
import json
import sys
import tarfile
from pathlib import Path

import pytest

from installer_world import make_world
from installer_world import run as install
from opncafyr.core.manifest import validate
from opncafyr.installer import cli, source_build
from opncafyr.installer import install as install_mod
from opncafyr.installer.pins import Pins
from opncafyr.installer.smoke import check_lib_paths
from opncafyr.installer.steps.r import NO_COMPILER, library_env
from opncafyr.installer.verify import InstallError

ROOT = Path(__file__).resolve().parents[1]

# --- the flag ---------------------------------------------------------------------------


def test_build_from_source_is_a_repair_option_only(capsys):
    args = cli.build_parser().parse_args(["repair", "--build-from-source"])
    assert args.build_from_source is True
    with pytest.raises(SystemExit) as exc:
        cli.build_parser().parse_args(["install", "--build-from-source"])
    assert exc.value.code == 2


def test_bundles_can_carry_the_source_build():
    args = cli.build_parser().parse_args(["bundle", "--out", "x", "--with-source-build"])
    assert args.with_source_build is True


# --- pins -------------------------------------------------------------------------------


@pytest.mark.parametrize("platform", ["macos-arm64", "macos-x86_64", "windows-x86_64"])
def test_real_pins_add_the_source_build_files(platform):
    pins = Pins.load(ROOT / "runtime")
    default = {a.path for a in pins.assets(platform)}
    extra = [a for a in pins.assets(platform, source_build=True) if a.path not in default]
    components = sorted(a.component for a in extra)
    expected = (
        ["opnca-r", "rtools", "rust"] if platform.startswith("windows") else ["opnca-r", "rust"]
    )
    assert components == expected
    vendored = next(a for a in extra if a.component == "opnca-r")
    assert vendored.file.endswith("-vendored.tar.gz") and vendored.private
    rust = next(a for a in extra if a.component == "rust")
    assert rust.version == pins.pins["rust"]["toolchain"] and rust.file.endswith(".tar.xz")


# --- a forced repair -------------------------------------------------------------------


def test_repair_with_the_flag_stages_a_new_install_and_records_it(tmp_path, layout, fakes):
    world = make_world(tmp_path / "w", source_build=True)
    assert install(layout, world.payload, assets=[world.assets]) == 0
    assert layout.read_current() == "0.0.1-1"
    messages: list[str] = []
    # A healthy install: a plain repair would change nothing, the flag always stages.
    assert (
        install(
            layout,
            world.payload,
            messages,
            mode="repair",
            build_from_source=True,
            assets=[world.assets],
        )
        == 0
    )
    assert layout.read_current() == "0.0.1-2"
    assert any("building opnca-r from source" in m for m in messages)
    record = layout.record("0.0.1-2")
    assert record["options"] == {"libreoffice": False, "build_from_source": True}
    validate(record, "install-v1")
    files = {f["file"] for c in record["components"] for f in c["files"]}
    assert "opnca_0.3.2-vendored.tar.gz" in files
    assert "rust-1.99.1-aarch64-apple-darwin.tar.xz" in files
    # A later install of the same version keeps the source-built install.
    again: list[str] = []
    assert install(layout, world.payload, again, assets=[world.assets]) == 0
    assert layout.read_current() == "0.0.1-2"
    assert any("already installed" in m for m in again)


def test_a_failed_source_build_keeps_the_previous_install(tmp_path, layout, fakes, monkeypatch):
    world = make_world(tmp_path / "w", source_build=True)
    assert install(layout, world.payload, assets=[world.assets]) == 0
    monkeypatch.setenv(install_mod.FAIL_ENV, "stage")
    with pytest.raises(InstallError, match="Previous install still active: 0.0.1-1"):
        install(layout, world.payload, mode="repair", build_from_source=True, assets=[world.assets])
    assert layout.read_current() == "0.0.1-1"


def test_a_missing_vendored_tarball_stops_before_anything_changes(tmp_path, layout, fakes):
    world = make_world(tmp_path / "w", source_build=True)
    assert install(layout, world.payload, assets=[world.assets]) == 0
    (world.assets / "opnca_0.3.2-vendored.tar.gz").unlink()
    with pytest.raises(InstallError, match="opnca-r"):
        install(
            layout,
            world.payload,
            mode="repair",
            build_from_source=True,
            assets=[world.assets],
        )
    assert layout.install_ids() == ["0.0.1-1"]


@pytest.mark.parametrize("victim", ["manifest.json", "opnca_0.3.2_R4.6_macos-arm64.tgz"])
def test_a_tampered_opnca_file_in_a_bundle_keeps_the_previous_install(
    tmp_path, layout, fakes, victim
):
    world = make_world(tmp_path / "w")
    assert install(layout, world.payload, from_bundle=world.bundle) == 0
    target = world.bundle / "assets" / "opnca-r" / victim
    target.write_bytes(target.read_bytes() + b" tampered")
    with pytest.raises(InstallError, match="opnca-r"):
        install(layout, world.payload, from_bundle=world.bundle, reinstall=True)
    assert layout.read_current() == "0.0.1-1"
    assert layout.install_ids() == ["0.0.1-1"]


@pytest.mark.skipif(sys.platform != "darwin", reason="the Command Line Tools are macOS only")
def test_macos_preflight_explains_missing_command_line_tools(tmp_path, layout, fakes, monkeypatch):
    world = make_world(tmp_path / "w", source_build=True)
    assert install(layout, world.payload, assets=[world.assets]) == 0
    monkeypatch.setattr(source_build, "XCODE_SELECT", tmp_path / "no-xcode-select")
    with pytest.raises(InstallError, match="Command Line Tools"):
        install(layout, world.payload, mode="repair", build_from_source=True, assets=[world.assets])
    assert layout.install_ids() == ["0.0.1-1"]


def test_compiler_dirs_cover_the_clt_and_xcode():
    dirs = source_build.compiler_dirs(Path("/Applications/Xcode.app/Contents/Developer"))
    assert Path("/Applications/Xcode.app/Contents/Developer/usr/bin") in dirs
    assert any("XcodeDefault.xctoolchain" in str(d) for d in dirs)


# --- the Rust toolchain ----------------------------------------------------------------


def _rust_archive(path: Path, components: list[str], exe: str = "") -> Path:
    top = "rust-1.99.1-test"
    with tarfile.open(path, "w:xz") as tar:

        def add(name: str, data: bytes, mode: int = 0o644) -> None:
            info = tarfile.TarInfo(f"{top}/{name}")
            info.size, info.mode = len(data), mode
            tar.addfile(info, io.BytesIO(data))

        add("components", ("\n".join(components) + "\n").encode())
        add("install.sh", b"#!/bin/sh\n", 0o755)
        for c in components:
            add(f"{c}/manifest.in", b"file:x\n")
            add(f"{c}/lib/{c}.txt", c.encode())
            if c in ("rustc", "cargo"):
                add(f"{c}/bin/{c}{exe}", b"#!/bin/sh\n", 0o755)
    return path


def test_rust_components_are_merged_without_install_sh(tmp_path):
    exe = ".exe" if sys.platform == "win32" else ""
    archive = _rust_archive(
        tmp_path / "rust.tar.xz",
        ["rustc", "cargo", "rust-std-aarch64-apple-darwin", "rust-docs", "clippy-preview"],
        exe,
    )
    dest = tmp_path / "rust"
    merged = source_build.merge_rust(archive, tmp_path / "work", dest)
    assert merged == ["rustc", "cargo", "rust-std-aarch64-apple-darwin"]
    assert (dest / "bin" / f"rustc{exe}").is_file() and (dest / "bin" / f"cargo{exe}").is_file()
    assert (dest / "lib" / "rust-std-aarch64-apple-darwin.txt").is_file()
    assert not (dest / "lib" / "rust-docs.txt").exists()
    assert not (dest / "manifest.in").exists() and not (tmp_path / "work").exists()


def test_an_archive_without_cargo_is_refused(tmp_path):
    archive = _rust_archive(tmp_path / "rust.tar.xz", ["rustc"])
    with pytest.raises(InstallError, match="rustc or cargo"):
        source_build.merge_rust(archive, tmp_path / "work", tmp_path / "rust")


def test_the_build_stays_offline_and_inside_its_folder(tmp_path):
    root, lib, rust = tmp_path / "b", tmp_path / "lib", tmp_path / "b" / "rust"
    path, env = source_build.build_env(root, lib, rust, None)
    assert path == [rust / "bin"]
    assert env["CARGO_NET_OFFLINE"] == "true"
    assert Path(env["CARGO_HOME"]).parent == root and Path(env["CARGO_TARGET_DIR"]).parent == root
    assert env["R_LIBS"] == env["R_LIBS_USER"] == str(lib)
    rtools = tmp_path / "b" / "rtools45"
    path, env = source_build.build_env(root, lib, rust, rtools)
    assert path[1:] == [rtools / "x86_64-w64-mingw32.static.posix" / "bin", rtools / "usr" / "bin"]
    assert env["RTOOLS45_HOME"] == str(rtools)


# --- the user's R library --------------------------------------------------------------


def test_binary_installs_use_only_the_app_library_and_no_compiler(tmp_path):
    lib = tmp_path / "rlib"
    assert library_env(lib) == {"R_LIBS": str(lib), "R_LIBS_USER": str(lib), "R_LIBS_SITE": ""}
    lines = NO_COMPILER.splitlines()
    assert {line.split(" = ")[0] for line in lines} >= {"CC", "CXX", "FC", "OBJC"}
    assert all(line.endswith("/nonexistent/opncafyr-installs-no-compiled-code") for line in lines)


def _lib_output(paths: list[str], home: str) -> str:
    return "\n".join(paths) + "\n--\n" + home


def test_isolation_check_accepts_the_app_and_r_libraries(tmp_path):
    lib = tmp_path / "rlib"
    lib.mkdir()
    home = "/opt/R/4.6"
    out = _lib_output([str(lib.resolve()), f"{home}/library"], home)
    assert check_lib_paths(out, str(lib)) == "2 library paths: the app's and R's own"


@pytest.mark.parametrize(
    "intruder",
    ["/Users/u/Library/R/arm64/4.6/library", "C:/Users/u/AppData/Local/R/win-library/4.6"],
)
def test_isolation_check_refuses_the_users_library(tmp_path, intruder):
    lib = tmp_path / "rlib"
    lib.mkdir()
    out = _lib_output([str(lib.resolve()), intruder, "/opt/R/4.6/library"], "/opt/R/4.6")
    with pytest.raises(RuntimeError, match="outside the app"):
        check_lib_paths(out, str(lib))


def test_isolation_check_needs_the_app_library(tmp_path):
    out = _lib_output(["/opt/R/4.6/library"], "/opt/R/4.6")
    with pytest.raises(RuntimeError):
        check_lib_paths(out, str(tmp_path / "rlib"))


def test_r_children_of_the_launcher_never_see_the_user_library(tmp_path):
    from opncafyr.adapters.deckifyr import cli_command
    from opncafyr.core.runtime import Runtime

    py = tmp_path / "python"
    py.write_text("")
    spec = cli_command(Runtime(executables={"deckifyr_python": py}, r_library=tmp_path), "build")
    assert spec.env["R_LIBS"] == spec.env["R_LIBS_USER"] == str(tmp_path)
    assert spec.env["R_LIBS_SITE"] == ""


def test_shipped_bundle_manifest_schema_accepts_the_flag():
    schema = json.loads((ROOT / "src/opncafyr/schemas/bundle-manifest-v1.schema.json").read_text())
    assert schema["properties"]["options"]["properties"]["source_build"] == {
        "type": "boolean",
        "description": schema["properties"]["options"]["properties"]["source_build"]["description"],
    }
    assert "source_build" not in schema["properties"]["options"]["required"]


@pytest.mark.skipif(sys.platform == "win32", reason="Windows uses 8.3 short names instead")
def test_the_build_folder_never_has_a_space(tmp_path):
    assert source_build.build_root(tmp_path / "plain") == tmp_path / "plain" / "source-build"
    with pytest.raises(InstallError, match="without spaces"):
        source_build.build_root(tmp_path / "with space")
