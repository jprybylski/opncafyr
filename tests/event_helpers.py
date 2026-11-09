"""Shared setup for the event-record tests (#58).

The standard setup, line partitions and planted cases come from
``fixtures/events.expected.json``; nothing here restates them.
"""

from __future__ import annotations

import copy
import json
from typing import Any

from fastapi.testclient import TestClient

from nca_helpers import FIXTURES, apply, new_recipe

EVENTS_EXPECTED = json.loads((FIXTURES / "events.expected.json").read_text("utf-8"))
MAIN = "nonmem_events.csv"
PLANTED = "nonmem_events_planted.csv"
GENERIC = "generic_single.csv"
SETUP = EVENTS_EXPECTED["standard_setup"]
EVENT_FILES = (MAIN, PLANTED, GENERIC)


def standard_events(**choices: Any) -> dict[str, Any]:
    """The standard events block; ``choices`` replace its choices when given."""
    events = copy.deepcopy(SETUP["events"])
    if choices:
        events["choices"] = {k: v for k, v in choices.items() if v is not None}
    return events


def planted_case(case_id: str) -> dict[str, Any]:
    return next(c for c in EVENTS_EXPECTED["files"][PLANTED]["cases"] if c["id"] == case_id)


def put_events(client: TestClient, recipe_id: str, events: dict[str, Any] | None) -> dict:
    r = client.put(f"/api/nca/recipes/{recipe_id}/events", json={"events": events})
    assert r.status_code == 200, r.text
    return r.json()


def event_recipe(
    client: TestClient,
    name: str = MAIN,
    events: dict[str, Any] | None = None,
    mapping: dict[str, Any] | None = None,
    steps: list[dict[str, Any]] | None = None,
) -> tuple[str, dict[str, Any]]:
    """A recipe with the standard steps and events block, applied; with the standard
    mapping unless ``mapping`` is given (``{}`` for none). Returns (id, result)."""
    doc = new_recipe(client, name, steps if steps is not None else SETUP["steps"])
    put_events(client, doc["id"], events if events is not None else standard_events())
    result = apply(client, doc["id"])
    use = SETUP["mapping"] if mapping is None else mapping
    if use:
        r = client.put(f"/api/nca/recipes/{doc['id']}/mapping", json={"mapping": use})
        assert r.status_code == 200, r.text
    return doc["id"], result
