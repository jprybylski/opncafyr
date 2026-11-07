"""quartifyr adapter: the bundled report template (#30, ADR 0007).

Executables are resolved from the installed runtime, never from workspace content.
``template/`` holds the report sources the launcher installs with the app; see its README.
"""

from __future__ import annotations

from importlib import resources
from importlib.resources.abc import Traversable


def template() -> Traversable:
    """The bundled report template folder (owner-maintained, from quartifyr's demo-report)."""
    return resources.files(__name__).joinpath("template")


def standard_footnotes() -> Traversable:
    """The bundled ``standard_footnotes.yaml``: abbreviations and footnote types."""
    return template().joinpath("standard_footnotes.yaml")
