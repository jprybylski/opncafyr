"""Native Linux install pieces (#65): platform, library preflight, .deb unpacking, R relocation,
the pending platform, and the pins a Linux install reads. No Linux needed: all of it is data."""

from __future__ import annotations

import io
import os
import subprocess
import tarfile
import tomllib
from pathlib import Path
from types import SimpleNamespace

import pytest

from opncafyr.installer import archives, linux_libs, platforms
from opncafyr.installer.pins import Pins, PinsError
from opncafyr.installer.sources import Cache, DownloadSource
from opncafyr.installer.steps import r as r_step

ROOT = Path(__file__).resolve().parents[1]
PINS = tomllib.loads((ROOT / "runtime/pins.toml").read_text("utf-8"))
UBUNTU = {"ID": "ubuntu", "VERSION_ID": "24.04", "PRETTY_NAME": "Ubuntu 24.04.1 LTS"}


# --- the platform ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("machine", "expected"),
    [("x86_64", "linux-x86_64"), ("amd64", "linux-x86_64"), ("aarch64", "linux-aarch64")],
)
def test_linux_machines_map_to_platforms(monkeypatch, machine, expected):
    monkeypatch.setattr(platforms.sys, "platform", "linux")
    monkeypatch.setattr(platforms._platform, "machine", lambda: machine)
    assert platforms.detect() == expected


def test_an_unknown_linux_machine_is_refused_with_the_matrix(monkeypatch):
    monkeypatch.setattr(platforms.sys, "platform", "linux")
    monkeypatch.setattr(platforms._platform, "machine", lambda: "riscv64")
    with pytest.raises(platforms.UnsupportedPlatform, match=r"Ubuntu 24.04 \(x86_64, aarch64\)"):
        platforms.detect()


def test_the_code_and_the_pins_agree_on_the_linux_baseline():
    pins = PINS["platforms"]
    assert pins["linux_os"] == platforms.LINUX_OS
    assert pins["linux_os_version"] == platforms.LINUX_OS_VERSION
    assert pins["linux_glibc_min"] == ".".join(map(str, platforms.LINUX_GLIBC_MIN))
    assert set(platforms.SUPPORTED) == set(pins["supported"])


def test_ubuntu_2404_with_glibc_239_passes():
    assert platforms.check_linux(UBUNTU, (2, 39)) == "Ubuntu 24.04, glibc 2.39"
    assert platforms.check_linux(UBUNTU, (2, 41)).endswith("glibc 2.41")


@pytest.mark.parametrize(
    ("release", "message"),
    [
        ({"ID": "ubuntu", "VERSION_ID": "22.04", "PRETTY_NAME": "Ubuntu 22.04"}, "Ubuntu 22.04"),
        ({"ID": "debian", "VERSION_ID": "12", "PRETTY_NAME": "Debian 12"}, "Debian 12"),
        ({}, "unknown Linux"),
    ],
)
def test_another_distribution_is_refused_and_pointed_to_the_container(release, message):
    with pytest.raises(platforms.UnsupportedPlatform) as err:
        platforms.check_linux(release, (2, 39))
    assert message in str(err.value) and "container" in str(err.value)


def test_an_older_or_unknown_c_library_is_refused():
    with pytest.raises(platforms.UnsupportedPlatform, match="glibc 2.39 or later.*glibc 2.35"):
        platforms.check_linux(UBUNTU, (2, 35))
    with pytest.raises(platforms.UnsupportedPlatform, match="could not be identified"):
        platforms.check_linux(UBUNTU, None)


def test_os_release_is_read_and_unquoted(tmp_path):
    path = tmp_path / "os-release"
    path.write_text('# c\nNAME="Ubuntu"\nID=ubuntu\nVERSION_ID="24.04"\n\nbad line\n', "utf-8")
    assert platforms.os_release(str(path)) == {
        "NAME": "Ubuntu",
        "ID": "ubuntu",
        "VERSION_ID": "24.04",
    }
    assert platforms.os_release(str(tmp_path / "missing")) == {}


# --- the system libraries --------------------------------------------------------------


def test_the_required_list_is_what_the_pinned_stack_links():
    names = [lib.soname for lib in linux_libs.REQUIRED]
    assert len(names) == len(set(names))
    # What R's own X11 and Tcl/Tk modules need is not required (opncafyr never loads them).
    assert not {"libX11.so.6", "libXt.so.6", "libtcl8.6.so", "libtk8.6.so"} & set(names)
    assert {"libcairo.so.2", "libfontconfig.so.1", "libgit2.so.1.7", "libicuuc.so.74"} <= set(names)
    assert all(lib.apt for lib in linux_libs.REQUIRED)


def test_nothing_is_missing_when_the_linker_knows_every_library():
    known = {lib.soname for lib in linux_libs.REQUIRED}
    assert linux_libs.missing_libraries(known=known) == []
    linux_libs.check_libraries(known=known)


def test_missing_libraries_are_named_with_the_packages_to_ask_for():
    known = {lib.soname for lib in linux_libs.REQUIRED} - {"libcairo.so.2", "libgit2.so.1.7"}
    with pytest.raises(linux_libs.MissingLibraries) as err:
        linux_libs.check_libraries(known=known)
    text = str(err.value)
    assert "libcairo.so.2" in text and "libgit2.so.1.7" in text
    assert "libcairo2 libgit2-1.7" in text and "administrator" in text
    assert "apt" not in text.split("install these Ubuntu packages")[0]  # it never runs apt


def test_a_package_that_provides_two_libraries_is_listed_once():
    known = {lib.soname for lib in linux_libs.REQUIRED} - {"libssl.so.3", "libcrypto.so.3"}
    with pytest.raises(linux_libs.MissingLibraries) as err:
        linux_libs.check_libraries(known=known)
    assert str(err.value).count("libssl3t64") == 1


def test_ldconfig_output_is_parsed(monkeypatch):
    out = "2 libs found:\n\tlibz.so.1 (libc6,AArch64) => /lib/aarch64-linux-gnu/libz.so.1\n"
    monkeypatch.setattr(linux_libs, "_ldconfig", lambda: "/sbin/ldconfig")
    done = subprocess.CompletedProcess([], 0, out.encode(), b"")
    monkeypatch.setattr(linux_libs.proc, "capture", lambda *a, **k: done)
    assert linux_libs.known_sonames() == {"libz.so.1"}


def test_without_ldconfig_the_standard_folders_are_searched(monkeypatch, tmp_path):
    monkeypatch.setattr(linux_libs, "known_sonames", lambda: None)
    monkeypatch.setattr(linux_libs, "_on_disk", lambda soname, machine: soname == "libz.so.1")
    missing = linux_libs.missing_libraries(
        [linux_libs.Lib("libz.so.1", "zlib1g"), linux_libs.Lib("libuv.so.1", "libuv1t64")]
    )
    assert [m.soname for m in missing] == ["libuv.so.1"]


def test_fonts_are_found_by_file_not_by_folder(tmp_path):
    assert not linux_libs.has_fonts([str(tmp_path / "none")])
    (tmp_path / "truetype").mkdir()
    assert not linux_libs.has_fonts([str(tmp_path)])
    (tmp_path / "truetype" / "x.ttf").write_bytes(b"")
    assert linux_libs.has_fonts([str(tmp_path)])


# --- unpacking Posit's .deb without dpkg -----------------------------------------------


def _deb(
    files: dict[str, bytes | None], *, link: tuple[str, str] | None = None, comp="gz"
) -> bytes:
    """A minimal .deb: debian-binary, control.tar.gz and a data tar. ``None`` makes a folder."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode=f"w:{comp}") as tar:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            if data is None:
                info.type = tarfile.DIRTYPE
                info.mode = 0o755
                tar.addfile(info)
            else:
                info.size = len(data)
                info.mode = 0o755 if name.endswith(("Rscript", "/R")) else 0o644
                tar.addfile(info, io.BytesIO(data))
        if link:
            info = tarfile.TarInfo(link[0])
            info.type = tarfile.SYMTYPE
            info.linkname = link[1]
            tar.addfile(info)
    data_tar = buf.getvalue()

    def member(name: str, body: bytes) -> bytes:
        head = f"{name + '/':<16}{0:<12}{0:<6}{0:<6}{100644:<8}{len(body):<10}`\n".encode()
        return head + body + (b"\n" if len(body) % 2 else b"")

    return (
        b"!<arch>\n"
        + member("debian-binary", b"2.0\n")
        + member("control.tar.gz", b"")
        + member(f"data.tar.{comp}", data_tar)
    )


R_TREE = {
    "./opt/": None,
    "./opt/R/": None,
    "./opt/R/4.6.2/": None,
    "./opt/R/4.6.2/bin/R": b'#!/bin/sh\nR_HOME_DIR="/opt/R/4.6.2/lib/R"\n',
    "./opt/R/4.6.2/bin/Rscript": b"\x7fELF compiled-in /opt/R/4.6.2/lib/R\0",
    "./opt/R/4.6.2/lib/R/bin/R": b"#!/bin/sh\nR_HOME_DIR=/opt/R/4.6.2/lib/R\n",
    "./opt/R/4.6.2/lib/R/bin/Rscript": b"\x7fELF compiled-in /opt/R/4.6.2/lib/R\0",
    "./opt/R/4.6.2/lib/R/etc/Makeconf": b"prefix = /opt/R/4.6.2\n",
    "./opt/R/4.6.2/lib/pkgconfig/libR.pc": b"prefix=/opt/R/4.6.2\n",
    "./opt/R/4.6.2/lib/R/library/base/DESCRIPTION": b"Package: base\n",
    "./usr/share/doc/r/copyright": b"not R\n",
}


def test_a_deb_unpacks_under_its_prefix_only(tmp_path):
    deb = tmp_path / "r.deb"
    deb.write_bytes(_deb(R_TREE))
    count = archives.extract_deb_tree(deb, "opt/R/4.6.2", tmp_path / "r")
    assert count == 7
    assert (tmp_path / "r" / "lib" / "R" / "library" / "base" / "DESCRIPTION").is_file()
    assert not (tmp_path / "r" / "usr").exists()
    assert os.access(tmp_path / "r" / "bin" / "R", os.X_OK)


def test_a_deb_with_a_link_or_an_escaping_path_is_refused(tmp_path):
    deb = tmp_path / "r.deb"
    deb.write_bytes(_deb(R_TREE, link=("./opt/R/4.6.2/bin/x", "/etc/passwd")))
    with pytest.raises(ValueError, match="link or special"):
        archives.extract_deb_tree(deb, "opt/R/4.6.2", tmp_path / "r")
    deb.write_bytes(_deb({"./opt/R/4.6.2/../../../evil": b"x"}))
    with pytest.raises(ValueError, match="escapes"):
        archives.extract_deb_tree(deb, "opt/R/4.6.2", tmp_path / "r2")


def test_not_a_deb_and_zstd_payloads_say_so(tmp_path):
    bad = tmp_path / "x.deb"
    bad.write_bytes(b"PK not an ar archive")
    with pytest.raises(ValueError, match="not a .deb"):
        archives.deb_data(bad)
    zst = tmp_path / "z.deb"
    blob = _deb(R_TREE).replace(b"data.tar.gz", b"data.tar.zst")
    zst.write_bytes(blob)
    with pytest.raises(ValueError, match="zstd"):
        archives.deb_data(zst)


def test_xz_payloads_are_read(tmp_path):
    deb = tmp_path / "r.deb"
    deb.write_bytes(_deb(R_TREE, comp="xz"))
    assert archives.extract_deb_tree(deb, "opt/R/4.6.2", tmp_path / "r") == 7


# --- the relocated R -------------------------------------------------------------------


def _ctx(tmp_path, deb: Path):
    pins = SimpleNamespace(r_version="4.6.2")
    return SimpleNamespace(
        r_dir=tmp_path / "versions" / "id" / "r",
        pins=pins,
        paths={},
        file=lambda name, *_: deb,
        asset=lambda name, *_: SimpleNamespace(file=deb.name),
    )


def test_linux_r_is_relocated_and_gets_rscript_wrappers(tmp_path):
    deb = tmp_path / "r-4.6.2.deb"
    # A count of at least 100 files guards against the wrong archive; pad the tree.
    tree = dict(R_TREE) | {f"./opt/R/4.6.2/lib/R/library/pkg{i}/f": b"x" for i in range(120)}
    deb.write_bytes(_deb(tree))
    ctx = _ctx(tmp_path, deb)
    rscript = r_step._install_linux(ctx)
    prefix = str(ctx.r_dir)
    assert rscript == ctx.r_dir / "bin" / "Rscript"
    assert prefix in (ctx.r_dir / "bin" / "R").read_text("utf-8")
    assert "/opt/R/" not in (ctx.r_dir / "lib" / "R" / "etc" / "Makeconf").read_text("utf-8")
    assert "/opt/R/" not in (ctx.r_dir / "lib" / "pkgconfig" / "libR.pc").read_text("utf-8")
    # The compiled-in Rscript is kept as Rscript.elf and replaced by a wrapper naming RHOME.
    elf = ctx.r_dir / "lib" / "R" / "bin" / "Rscript.elf"
    assert elf.read_bytes().startswith(b"\x7fELF")
    wrapper = (ctx.r_dir / "lib" / "R" / "bin" / "Rscript").read_text("utf-8")
    assert "RHOME=" in wrapper and 'exec "$RHOME/bin/Rscript.elf"' in wrapper
    outer = (ctx.r_dir / "bin" / "Rscript").read_text("utf-8")
    assert str(ctx.r_dir / "lib" / "R" / "bin" / "Rscript") in outer
    assert os.access(rscript, os.X_OK) and ctx.paths["r_home"] == ctx.r_dir / "lib" / "R"


def test_a_folder_with_a_space_is_refused(tmp_path):
    deb = tmp_path / "r.deb"
    deb.write_bytes(_deb(R_TREE))
    ctx = _ctx(tmp_path / "a b", deb)
    with pytest.raises(RuntimeError, match="space"):
        r_step._install_linux(ctx)


def test_a_deb_that_is_not_r_is_refused(tmp_path):
    deb = tmp_path / "r.deb"
    deb.write_bytes(_deb(R_TREE))  # too few files to be R
    with pytest.raises(RuntimeError, match="does not hold R 4.6.2"):
        r_step._install_linux(_ctx(tmp_path, deb))


def test_a_linux_binary_sits_at_the_source_path():
    url = "https://packagemanager.posit.co/cran/__linux__/noble/2026-11-06/src/contrib/cli_3.6.6.tar.gz"
    assert r_step.contrib_dir(url) == "src/contrib"


# --- the pins a Linux install reads ----------------------------------------------------


@pytest.fixture(scope="module")
def pins():
    return Pins.load(ROOT / "runtime")


@pytest.fixture
def pending(pins):
    """The pins as they were before opnca-r 0.3.3: Linux supported, but waiting for opnca-r."""
    waiting = Pins(pins.runtime_dir, dict(pins.pins), pins.lock, pins.binaries, pins.wheels)
    waiting.pins["platforms"] = {**pins.pins["platforms"], "pending": ["linux-x86_64"]}
    return waiting


@pytest.mark.parametrize(
    ("platform", "binary"),
    [
        ("linux-x86_64", "opnca_0.3.3_R4.6_linux-x86_64.tar.gz"),
        ("linux-aarch64", "opnca_0.3.3_R4.6_linux-arm64.tar.gz"),  # opnca-r says arm64
    ],
)
def test_linux_installs_opnca_r_from_its_published_binary(pins, platform, binary):
    assert pins.pending == []
    assets = pins.assets(platform)
    opnca = [a.file for a in assets if a.component == "opnca-r"]
    assert opnca == ["manifest.json", binary]
    parts = {a.component for a in assets}
    assert {"uv", "python", "r", "quarto", "r-packages", "wheels", "git", "quartifyr"} <= parts
    r = next(a for a in assets if a.component == "r")
    assert r.file.endswith(".deb") and "/ubuntu-2404/" in r.url  # one pin, read from [ci.*]
    assert r.version == PINS["r"]["version"]


def test_opnca_r_names_the_arm_architecture_arm64_where_the_platform_says_aarch64():
    assert platforms.opnca_name("linux-aarch64") == ("linux", "arm64")
    assert platforms.opnca_name("linux-x86_64") == ("linux", "x86_64")
    assert platforms.opnca_name("macos-arm64") == ("macos", "arm64")
    assert platforms.opnca_name("windows-x86_64") == ("windows", "x86_64")


def test_a_pending_platform_is_refused_until_opnca_r_has_a_binary(pending):
    with pytest.raises(PinsError, match=r"not released yet.*opnca-r#61"):
        pending.assets("linux-x86_64")
    assert pending.assets("linux-aarch64")  # only the listed platform waits


def test_a_development_install_of_a_pending_platform_has_everything_but_opnca_r(pending):
    assets = pending.assets("linux-x86_64", allow_pending=True)
    parts = {a.component for a in assets}
    assert "opnca-r" not in parts and {"r", "r-packages", "wheels", "git"} <= parts


@pytest.mark.parametrize("platform", ["linux-x86_64", "linux-aarch64"])
def test_linux_r_packages_carry_the_user_agent_that_selects_the_binary(pins, platform):
    packages = [a for a in pins.assets(platform) if a.component == "r-packages"]
    assert len(packages) == 126
    assert {a.user_agent for a in packages} == {pins.binaries["user_agents"][platform]}
    assert all("/__linux__/noble/" in a.url for a in packages)
    # Nothing else needs one: only R's own User-Agent gets a binary from P3M.
    others = [a for a in pins.assets(platform) if a.component != "r-packages"]
    assert not any(a.user_agent for a in others)


def test_other_platforms_are_unchanged_by_the_linux_pins(pins):
    assets = pins.assets("macos-arm64")
    assert not any(a.user_agent for a in assets)
    assert any(a.component == "opnca-r" for a in assets)


def test_libreoffice_and_a_source_build_are_refused_on_linux(pins):
    with pytest.raises(PinsError, match="LibreOffice is not offered"):
        pins.assets("linux-x86_64", libreoffice=True)
    with pytest.raises(PinsError, match="source build is not offered"):
        pins.assets("linux-x86_64", source_build=True)


def test_the_download_sends_the_pinned_user_agent(pins, tmp_path):
    source = DownloadSource(Cache(tmp_path / "c"), log=tmp_path / "dl.log", environ={})
    argv = source._argv("https://example.org/x", tmp_path / "x", "R (4.6.2 x86_64-pc-linux-gnu)")
    assert argv[argv.index("--user-agent") + 1] == "R (4.6.2 x86_64-pc-linux-gnu)"
    default = source._argv("https://example.org/x", tmp_path / "x")
    assert default[default.index("--user-agent") + 1] == "opncafyr-installer"


# --- the launcher script ---------------------------------------------------------------


def test_linux_gets_a_launcher_script_not_an_icon(tmp_path, monkeypatch):
    from opncafyr.installer import icons
    from opncafyr.installer.layout import Layout

    monkeypatch.setattr(icons.sys, "platform", "linux")
    layout = Layout(tmp_path / "root")
    assert not icons.present(layout)
    made = icons.create(layout, "id-1", "0.0.1", tmp_path / "icons.log")
    script = layout.root / "bin" / "opncafyr"
    assert made == [script] and os.access(script, os.X_OK) and icons.present(layout)
    text = script.read_text("utf-8")
    assert text.startswith("#!/bin/sh\n") and text.endswith(' -m opncafyr "$@"\n')
    # It goes through the current link, so an update needs no new script.
    assert str(layout.root / "current" / "launcher" / "bin" / "python") in text
    assert icons.existing() == []  # inside the app root: uninstall removes the root itself
    icons.remove(layout, tmp_path / "icons.log")
    assert not script.exists()
