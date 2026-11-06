"""opnca-r release selection and checks. The code lives in ``opncafyr.installer``.

The installer runs before any third-party package is installed, so the stdlib-only code
moved to ``opncafyr.installer.opnca_release``. This module keeps the old import path and
``python -m opncafyr.adapters.opnca.release`` working (CI, tests).
"""

from __future__ import annotations

import sys

from opncafyr.installer.opnca_release import (
    PIN_KEY,
    ReleaseError,
    host_platform,
    install_name,
    load_pins,
    main,
    select_asset,
    sha256_file,
    verify_asset,
    verify_manifest,
)

__all__ = [
    "PIN_KEY",
    "ReleaseError",
    "host_platform",
    "install_name",
    "load_pins",
    "main",
    "select_asset",
    "sha256_file",
    "verify_asset",
    "verify_manifest",
]

if __name__ == "__main__":
    sys.exit(main())
