"""The installed launcher icons use the opncafyr logo (#54)."""

from __future__ import annotations

import importlib.util
import plistlib
import struct
import sys
from importlib import resources
from pathlib import Path

import pytest

from opncafyr.installer import icons
from opncafyr.installer.layout import Layout

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "export_logo.py"
_spec = importlib.util.spec_from_file_location("export_logo", _SCRIPT)
export_logo = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(export_logo)


def _asset(name: str) -> bytes:
    return resources.files("opncafyr.installer").joinpath("assets", name).read_bytes()


def test_macos_app_carries_the_icon(tmp_path, monkeypatch):
    monkeypatch.setenv(icons.APPS_DIR_ENV, str(tmp_path / "Applications"))
    app = icons.write_macos_app(Layout(tmp_path / "root"), "0.0.1")
    contents = app / "Contents"
    plist = plistlib.loads((contents / "Info.plist").read_bytes())
    assert plist["CFBundleIconFile"] == icons.ICNS
    assert (contents / "Resources" / icons.ICNS).read_bytes() == _asset(icons.ICNS)


@pytest.mark.skipif(sys.platform != "darwin", reason="the .app exists on macOS only")
def test_an_app_without_its_icon_is_not_present(tmp_path, monkeypatch):
    """Repair recreates an .app made before the icon existed."""
    monkeypatch.setenv(icons.APPS_DIR_ENV, str(tmp_path / "Applications"))
    layout = Layout(tmp_path / "root")
    app = icons.write_macos_app(layout, "0.0.1")
    assert icons.present(layout)
    (app / "Contents" / "Resources" / icons.ICNS).unlink()
    assert not icons.present(layout)


def test_icns_holds_every_size_as_png():
    data = _asset(icons.ICNS)
    assert data[:4] == b"icns" and struct.unpack(">I", data[4:8])[0] == len(data)
    found, pos = {}, 8
    while pos < len(data):
        kind, length = data[pos : pos + 4], struct.unpack(">I", data[pos + 4 : pos + 8])[0]
        found[kind] = export_logo.png_size(data[pos + 8 : pos + length])
        pos += length
    assert found == {kind: (size, size) for kind, size in export_logo.ICNS_TYPES}


def test_ico_holds_every_size_as_png():
    data = _asset(icons.ICO)
    reserved, kind, count = struct.unpack("<HHH", data[:6])
    assert (reserved, kind, count) == (0, 1, len(export_logo.ICO_SIZES))
    sizes = []
    for i in range(count):
        w, h, _, _, _, _, length, offset = struct.unpack(
            "<BBBBHHII", data[6 + 16 * i : 22 + 16 * i]
        )
        png = export_logo.png_size(data[offset : offset + length])
        assert png == ((w or 256), (h or 256))
        sizes.append(png[0])
    assert sizes == list(export_logo.ICO_SIZES)


def test_logo_exports_match_their_svg_sources():
    """An SVG changed without rerunning scripts/export_logo.py, or an export was edited."""
    assert export_logo.check() == []
