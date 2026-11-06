"""A stand-in launcher for the kill-the-launcher test (tests/test_job_manager.py).

Usage: python job_launcher.py <workspace> <pids-file>

It opens the workspace, submits one job whose child starts a grandchild in its own session
(like processx and deckifyr do) and writes both pids, then sleeps until it is killed.
"""

import sys
import time
from pathlib import Path

from opncafyr.core.jobmanager import CommandSpec, JobManager, JobRequest

CHILD = (
    "import os, subprocess, sys, time\n"
    "kw = {'start_new_session': True} if os.name != 'nt' else {}\n"
    "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(600)'], **kw)\n"
    "open(sys.argv[1], 'w').write(f'{os.getpid()} {p.pid}')\n"
    "time.sleep(600)\n"
)


def main() -> None:
    workspace, pids = Path(sys.argv[1]), sys.argv[2]
    manager = JobManager()
    manager.open(workspace)
    manager.submit(
        JobRequest(
            kind="toy.orphan",
            label="orphan test",
            timeout_s=600,
            output="out/run1",
            build=lambda ctx: CommandSpec([sys.executable, "-c", CHILD, pids]),
        )
    )
    time.sleep(600)


if __name__ == "__main__":
    main()
