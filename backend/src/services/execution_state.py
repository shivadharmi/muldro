"""Execution state machine — enforces valid transitions for TaskRun and TaskStep.

Single source of truth for allowed status transitions in the execution engine.
Used by GraphExecutor, Operator, and recovery to validate state changes.
"""

import logging

logger = logging.getLogger(__name__)


# TaskRun allowed transitions (12 statuses)
RUN_TRANSITIONS: dict[str, set[str]] = {
    "pending": {"running", "cancelled", "blocked"},
    "running": {
        "paused",
        "awaiting_approval",
        "awaiting_input",
        "completed",
        "failed",
        "cancelled",
        "partially_completed",
    },
    "paused": {"running", "cancelled"},
    "awaiting_approval": {"running", "cancelled"},
    "awaiting_input": {"running", "cancelled"},
    "blocked": {"pending", "cancelled"},
    "partially_completed": {"running", "completed", "failed", "cancelled"},
    "completed": {"archived"},
    "failed": {"pending"},  # Retry: failed → pending
    "cancelled": set(),
    "archived": set(),
    "timed_out": {"pending", "cancelled"},
}

# TaskStep allowed transitions (10 statuses)
STEP_TRANSITIONS: dict[str, set[str]] = {
    "pending": {"ready", "skipped", "blocked"},
    "ready": {"running", "skipped"},
    "running": {
        "completed",
        "failed",
        "waiting_approval",
        "awaiting_input",
        "skipped",
        "timed_out",
    },
    "waiting_approval": {"running", "skipped"},
    "awaiting_input": {"running", "skipped", "cancelled"},
    "blocked": {"pending", "skipped"},
    "completed": set(),
    "failed": {"pending"},  # Retry: failed → pending
    "skipped": set(),
    "timed_out": {"pending", "skipped"},
}


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


def transition_run(run, new_status: str) -> None:
    """Transition a TaskRun to a new status, enforcing allowed transitions.

    Mutates run.status in place. Raises InvalidTransitionError if invalid.
    """
    allowed = RUN_TRANSITIONS.get(run.status, set())
    if new_status not in allowed:
        raise InvalidTransitionError("run", run.run_id, run.status, new_status)
    old = run.status
    run.status = new_status
    logger.debug("Run %s: %s → %s", run.run_id, old, new_status)


def transition_step(step, new_status: str) -> None:
    """Transition a TaskStep to a new status, enforcing allowed transitions.

    Mutates step.status in place. Raises InvalidTransitionError if invalid.
    """
    allowed = STEP_TRANSITIONS.get(step.status, set())
    if new_status not in allowed:
        raise InvalidTransitionError("step", step.step_id, step.status, new_status)
    old = step.status
    step.status = new_status
    logger.debug("Step %s: %s → %s", step.step_id, old, new_status)
