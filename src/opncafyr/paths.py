"""Per-user application paths.

Runtimes and app state live outside study workspaces:
- Windows: %LOCALAPPDATA%\\opncafyr
- macOS:   ~/Library/Application Support/opncafyr
"""

from __future__ import annotations

from pathlib import Path

from platformdirs import user_data_path

APP_NAME = "opncafyr"

# Name of the versioned metadata folder created inside a study workspace.
WORKSPACE_META_DIR = ".opncafyr"


def app_root() -> Path:
    """Root of app-owned files (runtimes, logs, state record). Never machine-wide."""
    return user_data_path(APP_NAME, appauthor=False, roaming=False)


def state_dir() -> Path:
    return app_root() / "state"


def logs_dir() -> Path:
    return app_root() / "logs"
