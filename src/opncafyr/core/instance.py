"""Single-instance lock and health/state record (stub).

Planned behaviour (docs/plan.md, Gate 1):
- A second icon click reopens the healthy existing instance instead of starting another.
- The state record stores PID, port and start time under ``paths.state_dir()``.
- A stale lock is removed only after the recorded PID and the health probe both fail.
"""

from __future__ import annotations
