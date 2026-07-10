"""Checkpoint retention-sweep tick (Step 6C CF-4).

Backstop for the INLINE reap-on-completion in ``AgentInvoker`` — which reaps a thread's
durable LangGraph checkpoints the moment its owning turn/step finishes. There are TWO inline
sites: (1) the chat path (``call_agent_stream`` / ``resume_deep_turn``) reaps a deep TURN
thread on any non-paused completion; (2) Step 10C P5's ``run_autonomous_deep_step`` reaps
each autonomous per-STEP thread on completion — that thread is never resumed (run-level
durable resume is via P4's reconcile), so the autonomous happy path leaves nothing for this
sweep to collect.

This periodic sweep is the substrate-agnostic DECIDED-APPROVAL backstop, keyed on
``Approval`` rows, covering both origins of a paused-then-resolved thread the inline reap can
never reach: a chat turn that paused on an approval, AND the rare Branch-C autonomous
within-step capability expansion that itself created a deep-gate ``Approval``. In both cases
the turn paused, was decided (approved/rejected/expired), but its resume never ran to a clean
completion. It reaps checkpoints for approvals decided older than a retention window, and
NEVER touches a thread with a still-PENDING approval.

DOCUMENTED RARE LIMITATION: a PRE-approved autonomous step thread carries NO ``Approval`` and
is not otherwise persisted, so a process-crash orphan of one is invisible to this
Approval-keyed sweep. That is an accepted edge; a proper age-based checkpoint-table sweep
(scan the checkpoints table directly, independent of any Approval) is a 10D refinement.

Gated three ways so it is a complete no-op on any non-deep or saver-less process:
* ``settings.runtime != "deep"`` → return (legacy runtime has no durable saver — and on a
  Redis-only effective-runtime flip the P2 saver, gated on ``settings.runtime == "deep"``,
  is likewise absent, so there is nothing durable to sweep either way);
* no durable saver reachable via the orchestrator's invoker → return (a worker may have been
  built without one);
* the saver lacks ``adelete_thread`` (e.g. MemorySaver) → the reaper itself no-ops.
"""

import logging

logger = logging.getLogger(__name__)


class CheckpointReaperTickMixin:
    """Periodic retention sweep of durable checkpoints for resolved approvals."""

    async def _tick_checkpoint_reaper(self, factory) -> None:
        """Reap durable checkpoints for approvals decided beyond the retention window."""
        if getattr(self._settings, "runtime", "legacy") != "deep":
            return
        saver = getattr(getattr(self._orchestrator, "_invoker", None), "checkpointer", None)
        if saver is None:
            return  # no durable saver reachable in this process → nothing to sweep
        from src.deep_runtime.checkpoint_reaper import sweep_decided_approval_checkpoints

        reaped = await sweep_decided_approval_checkpoints(saver, factory, retention_hours=24)
        if reaped:
            logger.info("checkpoint reaper swept %d resolved-approval thread(s)", reaped)
