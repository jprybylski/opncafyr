"""Job state machine.

queued -> preparing -> running -> reviewing -> completed, with failed and cancelled as
terminal states reachable from any non-terminal state. Only completed jobs may publish
artifacts to a report or deck (docs/spec.md, "One local origin owns every app").

Persistence, process-tree cancellation and human-readable progress live in the job
manager (#12, Gate 1).
"""

from __future__ import annotations

from enum import StrEnum


class JobState(StrEnum):
    QUEUED = "queued"
    PREPARING = "preparing"
    RUNNING = "running"
    REVIEWING = "reviewing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_STATES = frozenset({JobState.COMPLETED, JobState.FAILED, JobState.CANCELLED})

_FORWARD = {
    JobState.QUEUED: JobState.PREPARING,
    JobState.PREPARING: JobState.RUNNING,
    JobState.RUNNING: JobState.REVIEWING,
    JobState.REVIEWING: JobState.COMPLETED,
}


class JobStateError(RuntimeError):
    """Raised on an illegal state transition."""


def allowed_transitions(state: JobState) -> frozenset[JobState]:
    if state in TERMINAL_STATES:
        return frozenset()
    return frozenset({_FORWARD[state], JobState.FAILED, JobState.CANCELLED})


def transition(current: JobState, new: JobState) -> JobState:
    if new not in allowed_transitions(current):
        raise JobStateError(f"Illegal job transition {current} -> {new}.")
    return new


def can_publish(state: JobState) -> bool:
    return state is JobState.COMPLETED
