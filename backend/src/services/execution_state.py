"""Execution state machine — enforces valid transitions for TaskRun and TaskStep.

Single source of truth for allowed status transitions in the execution engine.
Used by GraphExecutor, the Executor, and recovery to validate state changes.
"""

import logging
from typing import Callable

logger = logging.getLogger(__name__)


# TaskRun allowed transitions (12 statuses)
RUN_TRANSITIONS: dict[str, set[str]] = {
    "pending": {"running", "cancelled", "blocked"},
    "running": {
        "paused",
        "awaiting_approval",
        "awaiting_input",
        "awaiting_reauth",
        "completed",
        "failed",
        "cancelled",
        "partially_completed",
    },
    "paused": {"running", "cancelled"},
    "awaiting_approval": {"running", "cancelled", "failed"},
    "awaiting_input": {"running", "cancelled", "failed"},
    # Run blocked on an OAuth provider the user must reconnect. Requeued to
    # pending (then re-run) once the credential is restored.
    "awaiting_reauth": {"pending", "running", "cancelled", "failed"},
    "blocked": {"pending", "cancelled"},
    "partially_completed": {"running", "completed", "failed", "cancelled"},
    "completed": {"archived"},
    "failed": {"pending"},  # Retry: failed → pending
    "cancelled": set(),
    "archived": set(),
    "timed_out": {"pending", "cancelled"},
}

# TaskStep allowed transitions.
# Step 3 adds two NET-NEW terminal-ish statuses (spec §4.5):
#   completed_unverified — write fired but read-back not yet confirmed (non-terminal
#     SUCCESS: upgradeable to completed on async confirm, or partially_completed on
#     async divergence).
#   partially_completed  — read-back CONTRADICTED the expected effect (surfaced +
#     escalate-first). Terminal for the step (compensation is a user-triggered re-run
#     that creates new steps, not an onward transition of this one).
STEP_TRANSITIONS: dict[str, set[str]] = {
    "pending": {"ready", "skipped", "blocked"},
    "ready": {"running", "skipped"},
    "running": {
        "completed",
        "completed_unverified",
        "partially_completed",
        "failed",
        "waiting_approval",
        "awaiting_input",
        "skipped",
        "timed_out",
        "cancelled",
    },
    "waiting_approval": {"running", "skipped"},
    "awaiting_input": {"running", "skipped", "cancelled"},
    # NOTE: there is no step-level ``awaiting_reauth`` state. OAuth re-auth
    # deferral is a RUN-level concern (see RUN_TRANSITIONS): the defer path
    # resets the blocked step to ``ready`` and parks the *run* in
    # ``awaiting_reauth``. A step never enters awaiting_reauth (M2).
    "blocked": {"pending", "skipped"},
    "completed": set(),
    # Deferred-read executor upgrades to completed on confirm, or partially_completed
    # on post-turn divergence.
    "completed_unverified": {"completed", "partially_completed"},
    "partially_completed": set(),
    "failed": {"pending"},  # Retry: failed → pending
    "skipped": set(),
    "cancelled": set(),
    "timed_out": {"pending", "skipped"},
}

# Step/run statuses that count as a terminal SUCCESS for progress, dependency
# satisfaction, and rollup counters (spec §4.5: "Replace every literal
# status == 'completed' counter with TERMINAL_SUCCESS membership, or a run whose last
# step is completed_unverified never reaches 100%"). NOTE: partially_completed is
# deliberately EXCLUDED — a diverged write is not a success. This set gates step-level
# counting only; run-level `completed` is unchanged (D6).
TERMINAL_SUCCESS: frozenset[str] = frozenset({"completed", "completed_unverified"})


class InvalidTransitionError(ValueError):
    """Raised when an invalid state transition is attempted."""

    def __init__(self, entity_type: str, entity_id: str, from_status: str, to_status: str):
        self.entity_type = entity_type
        self.entity_id = entity_id
        self.from_status = from_status
        self.to_status = to_status
        allowed = (
            RUN_TRANSITIONS.get(from_status, set())
            if entity_type == "run"
            else STEP_TRANSITIONS.get(from_status, set())
        )
        super().__init__(
            f"Invalid {entity_type} transition: {from_status} → {to_status} "
            f"(entity={entity_id}, allowed={allowed})"
        )


def transition_run(
    run,
    new_status: str,
    emit_event: Callable | None = None,
) -> None:
    """Transition a TaskRun to a new status, enforcing allowed transitions.

    Mutates run.status in place. Raises InvalidTransitionError if invalid.
    If emit_event callback is provided, emits a status_transition event.
    """
    allowed = RUN_TRANSITIONS.get(run.status, set())
    if new_status not in allowed:
        raise InvalidTransitionError("run", run.run_id, run.status, new_status)
    old = run.status
    run.status = new_status
    logger.debug("Run %s: %s → %s", run.run_id, old, new_status)

    if emit_event:
        try:
            emit_event(
                "run.status_changed",
                {
                    "run_id": run.run_id,
                    "from_status": old,
                    "to_status": new_status,
                },
            )
        except Exception:
            logger.debug("Failed to emit run transition event", exc_info=True)


def transition_step(
    step,
    new_status: str,
    emit_event: Callable | None = None,
) -> None:
    """Transition a TaskStep to a new status, enforcing allowed transitions.

    Mutates step.status in place. Raises InvalidTransitionError if invalid.
    If emit_event callback is provided, emits a status_transition event.
    """
    allowed = STEP_TRANSITIONS.get(step.status, set())
    if new_status not in allowed:
        raise InvalidTransitionError("step", step.step_id, step.status, new_status)
    old = step.status
    step.status = new_status
    logger.debug("Step %s: %s → %s", step.step_id, old, new_status)

    if emit_event:
        try:
            emit_event(
                "step.status_changed",
                {
                    "step_id": step.step_id,
                    "from_status": old,
                    "to_status": new_status,
                },
            )
        except Exception:
            logger.debug("Failed to emit step transition event", exc_info=True)
