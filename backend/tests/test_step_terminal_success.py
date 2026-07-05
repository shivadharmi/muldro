"""New step statuses (completed_unverified, step-level partially_completed) and the
TERMINAL_SUCCESS membership set. Pure — exercises the transition tables directly."""

import pytest

from src.services.execution_state import (
    STEP_TRANSITIONS,
    TERMINAL_SUCCESS,
    InvalidTransitionError,
    transition_step,
)


class _Step:
    def __init__(self, status):
        self.status = status
        self.step_id = "stp_test"


def test_terminal_success_membership():
    assert TERMINAL_SUCCESS == frozenset({"completed", "completed_unverified"})
    assert "completed" in TERMINAL_SUCCESS
    assert "completed_unverified" in TERMINAL_SUCCESS
    assert "partially_completed" not in TERMINAL_SUCCESS  # success-but-diverged is NOT success
    assert "failed" not in TERMINAL_SUCCESS


def test_running_can_transition_to_each_new_status():
    for target in ("completed_unverified", "partially_completed"):
        step = _Step("running")
        transition_step(step, target)
        assert step.status == target


def test_completed_unverified_upgrades_to_completed():
    step = _Step("running")
    transition_step(step, "completed_unverified")
    transition_step(step, "completed")  # async confirm upgrade
    assert step.status == "completed"


def test_completed_unverified_can_diverge_to_partially_completed():
    step = _Step("running")
    transition_step(step, "completed_unverified")
    transition_step(step, "partially_completed")  # async divergence
    assert step.status == "partially_completed"


def test_partially_completed_is_terminal_for_a_step():
    step = _Step("running")
    transition_step(step, "partially_completed")
    assert STEP_TRANSITIONS["partially_completed"] == set()


def test_completed_stays_terminal():
    step = _Step("completed")
    with pytest.raises(InvalidTransitionError):
        transition_step(step, "completed_unverified")  # can't un-verify a confirmed step


def test_terminal_success_covers_dependency_satisfaction_semantics():
    # A completed_unverified predecessor must satisfy a dependency the same as completed.
    done = {"completed", "completed_unverified"}
    assert all(s in TERMINAL_SUCCESS for s in done)
    assert "partially_completed" not in TERMINAL_SUCCESS  # a diverged step does NOT satisfy deps
    assert "failed" not in TERMINAL_SUCCESS
