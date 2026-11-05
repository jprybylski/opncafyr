"""opncafyr: a local, per-user workspace launcher for NCA runs and reporting."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("opncafyr")
except PackageNotFoundError:  # pragma: no cover - running from an uninstalled tree
    __version__ = "0.0.0"

__all__ = ["__version__"]
