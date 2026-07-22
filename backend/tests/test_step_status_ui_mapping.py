"""Regression tests for the DB-step-status → UI-StepState-status seam.

Reproduces the production 500 on GET /v1/surfaces/{id}/detail/steps where a step
parked in ``waiting_approval`` (a DB execution-state status) was fed straight into
the strict ``StepState.status`` Literal and raised a pydantic ValidationError.
"""

from src.contracts import StepState, step_status_to_ui
from src.services.execution_state import RUN_TRANSITIONS, STEP_TRANSITIONS

# Every DB status the execution state machine can produce for a step.
ALL_DB_STEP_STATUSES = sorted(
    set(STEP_TRANSITIONS) | {dst for dsts in STEP_TRANSITIONS.values() for dst in dsts}
)


def test_mapping_covers_every_db_step_status_and_yields_valid_ui_literal():
    for db_status in ALL_DB_STEP_STATUSES:
        ui_status = step_status_to_ui(db_status)
        # Must construct without raising — i.e. ui_status is a valid StepState literal.
        state = StepState(step_id="step_x", description="d", status=ui_status)
        assert state.status == ui_status


def test_waiting_approval_maps_to_approval_needed():
    assert step_status_to_ui("waiting_approval") == "approval_needed"


def test_known_terminal_and_active_statuses():
    assert step_status_to_ui("running") == "executing"
    assert step_status_to_ui("awaiting_input") == "user_action"
    assert step_status_to_ui("timed_out") == "failed"
    assert step_status_to_ui("cancelled") == "failed"


def test_none_and_unknown_fall_back_to_pending():
    assert step_status_to_ui(None) == "pending"
    assert step_status_to_ui("some_future_status") == "pending"


def test_step3_verification_statuses_pass_through():
    # The verification nuance now reaches the UI (frontend renders ✓? and ⚠),
    # so the backend no longer collapses these two — they pass through as-is.
    assert step_status_to_ui("completed_unverified") == "completed_unverified"
    assert step_status_to_ui("partially_completed") == "partially_completed"
    # And StepState must accept them (widened Literal).
    for s in ("completed_unverified", "partially_completed"):
        assert StepState(step_id="s", description="d", status=s).status == s


def test_run_machine_unchanged_sanity():
    # Guard: awaiting_approval is still not self-allowed (documents Bug 1 invariant).
    assert "awaiting_approval" not in RUN_TRANSITIONS["awaiting_approval"]
