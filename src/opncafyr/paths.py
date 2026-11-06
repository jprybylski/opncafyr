"""Per-user application paths.

Runtimes and app state live outside study workspaces:
- Windows: %LOCALAPPDATA%\\opncafyr
- macOS:   ~/Library/Application Support/opncafyr

A launcher started from an install (``<root>/versions/<id>/launcher``) uses that install's
root, so an install made under a different root (tests, CI) is self-contained.
``OPNCAFYR_APP_ROOT`` overrides both (tests and CI only).
"""

from __future__ import annotations

import os
from pathlib import Path

from platformdirs import user_data_path

from opncafyr.installer.layout import APP_ROOT_ENV, installed_root, installed_version_dir

APP_NAME = "opncafyr"

# Name of the versioned metadata folder created inside a study workspace.
WORKSPACE_META_DIR = ".opncafyr"


def default_app_root() -> Path:
    """The platform's per-user data folder for opncafyr (no install or override involved)."""
    return user_data_path(APP_NAME, appauthor=False, roaming=False)


def app_root() -> Path:
    """Root of app-owned files (runtimes, logs, state record). Never machine-wide."""
    override = os.environ.get(APP_ROOT_ENV)
    if override:
        return Path(override)
    return installed_root() or default_app_root()


def active_install_dir() -> Path | None:
    """``versions/<id>`` this launcher runs from, if it runs from an install."""
    return installed_version_dir()


def state_dir() -> Path:
    return app_root() / "state"


def logs_dir() -> Path:
    return app_root() / "logs"
