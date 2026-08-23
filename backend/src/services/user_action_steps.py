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
from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any

from src.services.execution_state import transition_run, transition_step

logger = logging.getLogger(__name__)

__all__ = [
    "USER_ACTION_TASK_TYPE",
    "handle_user_action_step",
    "park_if_blocked_on_founder",
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


# Step statuses that mean the run is already waiting on something OTHER than
# the founder's own steps — an approval, or a reconnect. Those paths park the
# run themselves, and re-parking it here would relabel their wait as ours.
_OTHER_WAITS = frozenset({"waiting_approval", "awaiting_input", "awaiting_reauth"})

# Statuses a step can still leave under its own power.
_STILL_MOVING = frozenset({"pending", "ready", "running"})


async def park_if_blocked_on_founder(run: Any, all_steps: Sequence[Any], emitter: Any) -> bool:
    """Park a run whose only remaining blocker is a step the founder owns.

    A skipped step is NOT in `TERMINAL_SUCCESS`, and `get_ready_steps` releases
    a step only when every dependency is. So a dependent of a skipped user
    action can never become ready: the DAG loop finds nothing ready, sees
    pending steps and no failures, and falls out through a bare `break` that
    performs no terminal transition at all. The run then sits in `running` for
    ever with its remaining steps `pending` — invisible, and indistinguishable
    from work still in progress.

    `awaiting_input` is the honest status and it already carries the whole
    chain: it is a legal transition out of `running`, `domain_units._ACTIVE`
    selects it, and `_RUN_STATUS` maps it to the `needs_you` frame status. The
    run becomes a card that says it is waiting on the founder, keeping
    everything it already did.

    This does NOT reintroduce the deadlock `presence` and PREPARE exist to
    prevent. That harm was an approval expiring unanswered and cancelling a run
    at 0/N steps, having achieved nothing. Here the run has done everything it
    can, and what remains is genuinely the founder's. Parking visibly is the
    truth; the run is not cancelled and nothing expires.

    Returns whether it parked the run.
    """
    if not any(is_user_action_step(s) and s.status == "skipped" for s in all_steps):
        return False
    if any(s.status in _OTHER_WAITS for s in all_steps):
        return False
    if not any(s.status in _STILL_MOVING for s in all_steps):
        return False
    if run.status != "running":
        return False

    transition_run(run, "awaiting_input")
    logger.info(
        "Run %s has nothing left it can do without the founder — parking it as "
        "awaiting_input rather than leaving it to look like work in progress",
        getattr(run, "run_id", "?"),
    )
    await emitter.emit_event(
        "run.awaiting_input",
        run.user_id,
        {"run_id": run.run_id, "reason": "user_action"},
        workspace_id=run.workspace_id,
    )
    return True
