"""Hold the launcher lock in a separate process for ``tests/test_instance.py``.

Usage: ``python instance_holder.py <state_dir> <seconds>``. Prints ``locked`` once the
lock is held, then exits normally (code 0) after ``seconds``.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

from opncafyr.core.instance import InstanceLock

if __name__ == "__main__":
    lock = InstanceLock(Path(sys.argv[1]))
    if not lock.try_acquire():
        sys.exit(2)
    print("locked", flush=True)
    time.sleep(float(sys.argv[2]))
    lock.release()
