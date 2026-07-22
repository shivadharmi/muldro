"""Reconcile-from-event-log consumer (Step 10C P4).

A durable resume boundary can find the mutable ``TaskRun``/``TaskStep`` truth rows
BEHIND the ``runtime_events`` log: a process crash between the external effect and the
DB completion write leaves the log recording a ``step_completed`` the row never got.
The log is the system-of-record *where it is ahead* — this module applies the log onto
the truth rows, UP-ONLY, via the execution state machine (never direct mutation), so a
durable resume can rebuild run/step state from the log alone.

Substrate-agnostic: ``RuntimeProjectionService.rebuild_run_projection`` folds only event
types + payloads (never checkpoint/substrate state; Spike 0.2), so this reconcile brings
an in-flight DEEP autonomous run back onto a LEGACY resume — the primitive Step 10D's
cross-substrate drain needs.

Invariants:
  * UP-ONLY — upgrades a behind step to ``completed``; NEVER downgrades a step already in
    ``TERMINAL_SUCCESS`` (the log is authoritative only where it is ahead).
  * State-machine-guarded — every change goes through ``transition_step`` /
    ``transition_run``; a rejected transition (e.g. the DB row shows a step the log says
    completed that never even started) is skipped, not forced. That gap is a genuine one
    the DAG re-pick + idempotency ledger handle on resume.
  * Conservative run status — only upgrades the run to a terminal-success status the log
    actually recorded (``completed`` / ``partially_completed``) when every step row is now
    terminal. A ``None`` log status (the 0.2 failed-branch case, where a run went
    ``failed`` without a ``run_failed`` event) or an incomplete log leaves the run
    untouched for the DAG to re-pick and complete normally. NEVER regresses a terminal run.

The CALLER owns the transaction: this only mutates session-tracked rows and relies on the
caller's flush/commit, matching how ``GraphExecutor._resume_run_body`` manages its session.
"""

import logging

from sqlalchemy import select

from src.models.task_graph import TaskStep
from src.services.execution_state import (
    TERMINAL_SUCCESS,
    InvalidTransitionError,
    transition_run,
    transition_step,
)
from src.services.runtime_projection import RuntimeProjectionService

logger = logging.getLogger(__name__)

# Run-level terminal-success statuses the reconcile may upgrade a run INTO from the log.
# Distinct from the step-level TERMINAL_SUCCESS: a run's ``partially_completed`` is a
# terminal-success OUTCOME (some writes diverged) even though the step-level set excludes
# it; ``completed_unverified`` is a STEP status and is never a run status.
_RUN_TERMINAL_SUCCESS: frozenset[str] = frozenset({"completed", "partially_completed"})

# A step row is "terminal" (no onward work) in any of these — used only as the
# "every step is now terminal" guard on the conservative run-status upgrade. Excludes
# ``failed`` / ``timed_out`` (retryable: they must BLOCK a run-complete upgrade).
_TERMINAL_STEP_STATUSES: frozenset[str] = frozenset(
    {"completed", "completed_unverified", "partially_completed", "skipped", "cancelled"}
)


async def reconcile_run_from_events(db, run) -> dict:
    """Reconcile a run's TaskRun/TaskStep rows from the runtime_events log at a resume
    boundary (Step 10C P4). The log is the system-of-record where it is AHEAD of the rows
    (a crash lost the DB completion write); this applies the log to the truth rows via
    transition_run/transition_step (NEVER direct mutation), UP-ONLY: it upgrades a behind
    step to completed but NEVER downgrades a terminal-success step (the log is authoritative
    only where it is ahead). Substrate-agnostic (reads event types) — this is what lets 10D
    drain an in-flight DEEP run onto a LEGACY resume. Returns a summary of what it reconciled."""
    service = RuntimeProjectionService(db, workspace_id=run.workspace_id)
    proj = await service.rebuild_run_projection(run.run_id)
    log_completed = set(proj.get("completed_step_ids") or [])

    steps = (
        (await db.execute(select(TaskStep).where(TaskStep.run_id == run.run_id))).scalars().all()
    )

    # ── UP-ONLY step reconcile ──────────────────────────────────────────────────────
    reconciled = 0
    for step in steps:
        if step.step_id in log_completed and step.status not in TERMINAL_SUCCESS:
            try:
                transition_step(step, "completed")
                reconciled += 1
            except InvalidTransitionError:
                # The log says this step completed, but the DB row's status has no path to
                # ``completed`` (e.g. it never started — ``pending``). Do NOT force an
                # invalid state: a genuine gap the DAG re-pick + idempotency ledger handle.
                logger.debug(
                    "reconcile: run %s step %s log-completed but %s→completed rejected; "
                    "leaving for re-pick",
                    run.run_id,
                    step.step_id,
                    step.status,
                )

    # ── Conservative run-status upgrade ─────────────────────────────────────────────
    log_status = proj.get("status")
    all_steps_terminal = bool(steps) and all(s.status in _TERMINAL_STEP_STATUSES for s in steps)
    if (
        log_status in _RUN_TERMINAL_SUCCESS
        # P4 review LOW: guard on the RUN-level terminal-success set (not the step-level
        # TERMINAL_SUCCESS) so a run already ``partially_completed`` is never promoted to
        # ``completed`` — that would erase the divergence/escalate-first signal. Not reachable
        # from the sole caller (pre-filters to resumable statuses) but defense-in-depth.
        and run.status not in _RUN_TERMINAL_SUCCESS
        and proj["total_steps"] > 0
        and proj["completed_steps"] >= proj["total_steps"]
        and all_steps_terminal
    ):
        try:
            transition_run(run, log_status)
        except InvalidTransitionError:
            # The run's current (resumable) status has no direct path to the terminal log
            # status (e.g. paused→completed). Leave it: the caller transitions to running
            # next and the DAG completes it normally now that every step is terminal.
            logger.debug(
                "reconcile: run %s log-status %s not a valid transition from %s; "
                "leaving for re-pick",
                run.run_id,
                log_status,
                run.status,
            )

    return {
        "reconciled_steps": reconciled,
        "log_status": log_status,
        "log_completed": len(log_completed),
    }
