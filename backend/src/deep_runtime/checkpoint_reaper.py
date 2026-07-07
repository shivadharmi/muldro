"""Checkpoint reaper for the durable LangGraph saver (Step 6C CF-4).

durability="sync" writes a checkpoint every superstep; without a reaper the
checkpoints / checkpoint_writes / checkpoint_blobs tables grow unbounded. Two reapers:

* reap_thread(saver, thread_id): delete ONE thread's checkpoints — called after a deep turn
  completes WITHOUT pausing (a paused turn keeps its checkpoint until resume).
* sweep_decided_approval_checkpoints(...): a periodic backstop that reaps checkpoints for
  DECIDED approvals older than a retention window — catches paused-then-resolved threads that
  were never resumed. Never touches a thread with a still-PENDING approval.

All gated on a saver that exposes adelete_thread (the durable AsyncPostgresSaver; the legacy
MemorySaver has none → these are no-ops).
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def reap_thread(saver, thread_id: str) -> bool:
    """Best-effort delete of one thread's checkpoints. Returns True if a delete was attempted.
    No-op (returns False) when the saver is None or lacks adelete_thread (e.g. MemorySaver)."""
    if saver is None or not hasattr(saver, "adelete_thread") or not thread_id:
        return False
    try:
        await saver.adelete_thread(thread_id)
        return True
    except Exception:
        logger.debug("checkpoint reap failed for thread %s", thread_id, exc_info=True)
        return False


async def sweep_decided_approval_checkpoints(
    saver, db_factory, *, retention_hours: int = 24, now=None
) -> int:
    """Reap checkpoints for approvals decided > retention_hours ago. Guard: only DECIDED
    (approved/rejected/expired) approvals are swept — a still-PENDING approval's thread is
    never touched. Returns the number of threads reaped."""
    if saver is None or not hasattr(saver, "adelete_thread"):
        return 0
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import select

    from src.models.approvals import Approval

    cutoff = (now or datetime.now(timezone.utc)) - timedelta(hours=retention_hours)
    async with db_factory() as db:
        stmt = select(Approval.thread_id).where(
            Approval.status.in_(("approved", "rejected", "expired")),
            Approval.decided_at.is_not(None),
            Approval.decided_at < cutoff,
            Approval.thread_id.is_not(None),
        )
        result = await db.execute(stmt)
        thread_ids = [t for t in result.scalars().all() if t]
    reaped = 0
    for tid in set(thread_ids):
        if await reap_thread(saver, tid):
            reaped += 1
    return reaped
