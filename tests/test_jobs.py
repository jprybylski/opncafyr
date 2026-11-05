import pytest

from opncafyr.core.jobs import (
    TERMINAL_STATES,
    JobState,
    JobStateError,
    allowed_transitions,
    can_publish,
    transition,
)

HAPPY_PATH = ["queued", "preparing", "running", "reviewing", "completed"]


def test_happy_path():
    state = JobState.QUEUED
    for nxt in HAPPY_PATH[1:]:
        state = transition(state, JobState(nxt))
    assert state is JobState.COMPLETED


@pytest.mark.parametrize("state", [s for s in JobState if s not in TERMINAL_STATES])
def test_any_live_state_can_fail_or_cancel(state):
    assert {JobState.FAILED, JobState.CANCELLED} <= allowed_transitions(state)


@pytest.mark.parametrize("state", sorted(TERMINAL_STATES))
def test_terminal_states_are_final(state):
    assert allowed_transitions(state) == frozenset()
    with pytest.raises(JobStateError):
        transition(state, JobState.RUNNING)


def test_cannot_skip_review():
    with pytest.raises(JobStateError):
        transition(JobState.RUNNING, JobState.COMPLETED)


def test_only_completed_publishes():
    assert [s for s in JobState if can_publish(s)] == [JobState.COMPLETED]
