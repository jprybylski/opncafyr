"""``python -m opncafyr.installer status|uninstall`` (install and repair need a payload)."""

import sys

from opncafyr.installer.cli import main

sys.exit(main())
