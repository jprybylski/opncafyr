"""Reverse proxy for managed child apps under ``/apps/<tool>/`` (stub).

Planned behaviour (docs/plan.md, Gate 1):
- HTTP proxying to a child bound on its own private loopback port; WebSocket support only if
  a child needs it (neither opnca-r nor deckifyr does today).
- The preferred fix for prefixes is a configurable base path in each child; any rewrite here
  is temporary and must pass the automated route checks.
- Child apps never see the session secret; the controller re-checks Host/Origin itself.
"""

from __future__ import annotations
