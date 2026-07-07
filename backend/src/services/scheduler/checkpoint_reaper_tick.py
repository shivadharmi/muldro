"""Checkpoint retention-sweep tick (Step 6C CF-4).

Backstop for the primary reaper in ``AgentInvoker`` (which reaps a thread's durable
LangGraph checkpoints the moment a deep turn finishes without pausing). This periodic
sweep catches the paused-then-resolved threads a completion reap can never reach: a turn
that paused on an approval, was decided (approved/rejected/expired), but whose resume never
ran to a clean completion. It reaps checkpoints for approvals decided older than a retention
window, and NEVER touches a thread with a still-PENDING approval.

Gated three ways so it is a complete no-op on any non-deep or saver-less process:
* ``settings.runtime != "deep"`` → return (legacy runtime has no durable saver);
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
