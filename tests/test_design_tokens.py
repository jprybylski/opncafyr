"""The colour tokens in app.css (#50): both dark blocks agree and every pair meets AA."""

import re
from importlib import resources
from pathlib import Path

import pytest

CSS = Path(str(resources.files("opncafyr.web").joinpath("static/app.css"))).read_text("utf-8")

# Foreground token, background token. Text must reach 4.5:1 (WCAG 2.2 AA, 1.4.3).
TEXT_PAIRS = [
    ("fg", "bg"),
    ("fg", "surface"),
    ("fg", "surface-2"),
    ("fg", "nav-bg"),
    ("fg", "nav-hover"),
    ("fg", "row-hover"),
    ("muted", "bg"),
    ("muted", "surface"),
    ("muted", "surface-2"),
    ("muted", "nav-bg"),
    ("muted", "row-hover"),
    ("accent", "bg"),
    ("accent", "surface"),
    ("accent", "surface-2"),
    ("accent", "accent-soft"),
    ("accent-fg", "accent"),
    ("accent-fg", "accent-hover"),
    ("accent-strong", "accent-soft"),
    ("chrome-fg", "chrome-bg"),
    ("chrome-fg", "chrome-hover"),
    ("chrome-muted", "chrome-bg"),
    ("chrome-bg", "chrome-accent"),
    ("danger", "surface"),
    ("danger", "danger-soft"),
    ("ok", "ok-soft"),
    ("warn", "warn-soft"),
    ("info", "info-soft"),
]
# Borders and focus rings that identify a control need 3:1 (1.4.11).
UI_PAIRS = [
    ("border-strong", "surface"),
    ("border-strong", "bg"),
    ("accent", "bg"),
    ("chrome-accent", "chrome-bg"),
]


def _block(start: str) -> str:
    i = CSS.index(start)
    return CSS[i : CSS.index("}", i)]


def _tokens(block: str) -> dict[str, str]:
    return dict(re.findall(r"--([a-z0-9-]+):\s*([^;]+);", block))


LIGHT = _tokens(_block(":root {"))
DARK_OS = _tokens(_block(':root:not([data-theme="light"]) {'))
DARK_PINNED = _tokens(_block(':root[data-theme="dark"] {'))
THEMES = {"light": LIGHT, "dark": {**LIGHT, **DARK_PINNED}}


def _luminance(hex_colour: str) -> float:
    h = hex_colour.strip().lstrip("#")
    assert re.fullmatch(r"[0-9a-f]{6}", h), f"not a #rrggbb colour: {hex_colour}"
    channels = [int(h[i : i + 2], 16) / 255 for i in (0, 2, 4)]
    lin = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def contrast(a: str, b: str) -> float:
    la, lb = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def test_contrast_formula():
    assert contrast("#000000", "#ffffff") == pytest.approx(21.0)
    assert contrast("#777777", "#ffffff") == pytest.approx(4.48, abs=0.01)


def test_dark_blocks_are_identical():
    assert DARK_OS == DARK_PINNED


def test_every_colour_is_first_defined_on_root():
    # A token defined only in a dark block would be missing in light mode.
    assert set(DARK_PINNED) <= set(LIGHT)


@pytest.mark.parametrize("theme", sorted(THEMES))
@pytest.mark.parametrize(
    ("fg", "bg", "minimum"), [(*p, 4.5) for p in TEXT_PAIRS] + [(*p, 3.0) for p in UI_PAIRS]
)
def test_pairs_meet_wcag_aa(theme, fg, bg, minimum):
    tokens = THEMES[theme]
    ratio = contrast(tokens[fg], tokens[bg])
    assert ratio >= minimum, f"{theme}: --{fg} on --{bg} is {ratio:.2f}:1, needs {minimum}:1"
