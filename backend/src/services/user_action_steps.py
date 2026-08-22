"""Steps the FOUNDER must perform, not muldro.

`plan_store` persists a `user` actor step as a PlanTask with
``task_type="user_action"`` and ``status="awaiting_input"`` — correctly.
`step_graph_store` forwarded that into the TaskStep's ``input_data`` and left
the ``step_type`` COLUMN null, and nothing in `dag_runner` branched on either.
So the DAG ran user steps through an agent and marked them completed.

Observed on one seven-step autonomous run:

    "User reviews the triage summary and confirms which items are important"
        -> completed
    "Create planning goals for the APPROVED important items"
        -> completed

Nobody reviewed anything. The run acted on a confirmation that never happened,
and the record says the founder gave it. That is worse than a missing prompt:
it manufactures the appearance of one, and it is the same record trust
graduation and the audit trail are later read from.

Lives here rather than in `dag_runner` because that file is at its size cap.
"""

import logging
from datetime import datetime, timezone
from typing import Any

from src.services.execution_state import transition_step

logger = logging.getLogger(__name__)

__all__ = [
    "USER_ACTION_TASK_TYPE",
    "handle_user_action_step",
    "is_user_action_step",
]

USER_ACTION_TASK_TYPE = "user_action"


def is_user_action_step(step: Any) -> bool:
    """Whether this step is the founder's to perform rather than muldro's.

    Reads BOTH the column and the payload: the column is the right home and is
    now populated, but runs created before that change carry the fact only in
    ``input_data``, and a step nobody can classify must not be executed on the
    strength of where the field happened to live.
    """
    if getattr(step, "step_type", None) == USER_ACTION_TASK_TYPE:
        return True
    payload = getattr(step, "input_data", None) or {}
    return payload.get("task_type") == USER_ACTION_TASK_TYPE


async def handle_user_action_step(step: Any, run: Any, emitter: Any, db: Any) -> bool:
    """Record a founder-owned step as skipped. False when it is not one.

    Returns whether it handled the step, so the caller is one `if` and a
    `return` — `dag_runner` is at its size cap and this is a whole concern.

    Muldro did not perform it, and says so.

    ``skipped``, deliberately, NOT ``awaiting_input``. Parking there would
    freeze an autonomous run on a human who is not there — the exact deadlock
    ``presence`` and PREPARE exist to prevent — and dependents already proceed
    past a skip today. This changes only the CLAIM: the step no longer says the
    founder did something they did not.

    Whether dependents should proceed past an unanswered user action is a
    separate question, and a real one.
    """
    if not is_user_action_step(step):
        return False
    logger.info(
        "Step %s is the founder's to perform (%s) — recording it as skipped",
        getattr(step, "step_id", "?"),
        getattr(step, "name", ""),
    )
    transition_step(step, "running")
    transition_step(step, "skipped")
    step.completed_at = datetime.now(timezone.utc)
    step.output_data = {"user_action": True, "performed": False}
    await db.flush()
    await emitter.emit_event(
        "step.skipped",
        run.user_id,
        {"run_id": run.run_id, "step_id": step.step_id, "reason": "user_action"},
        workspace_id=run.workspace_id,
    )
    return True
