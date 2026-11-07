"""Where the user guide's files are (#40).

The guide is written in ``docs/user-guide/``. The wheel ships that folder as
``opncafyr/web/guide/`` (``pyproject.toml``, ``force-include``), and the controller serves
it under ``/static/guide/`` for the home page's Guide view. An editable development
install has no packaged copy, so it falls back to the repository folder.
"""

from __future__ import annotations

from importlib import resources
from pathlib import Path

REPO_GUIDE = Path(__file__).resolve().parents[3] / "docs" / "user-guide"


def guide_root() -> Path | None:
    """The folder holding ``index.md``, or ``None`` when no guide is installed."""
    packaged = Path(str(resources.files("opncafyr.web").joinpath("guide")))
    for folder in (packaged, REPO_GUIDE):
        if (folder / "index.md").is_file():
            return folder
    return None
