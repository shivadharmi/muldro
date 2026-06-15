"""Dead-letter queue retry and per-operation re-dispatch."""

import logging

from src.models.task_graph import TaskRun
from src.services.dead_letter import DeadLetterService
from src.services.execution_state import transition_run

logger = logging.getLogger(__name__)


class DlqTickMixin:
    """Retries DLQ entries and re-dispatches them by operation_type."""

    async def _tick_dlq_retry(self, factory) -> None:
        """Retry DLQ entries that haven't exceeded max attempts."""
        try:
            async with factory() as db:
                dlq = DeadLetterService(db)
                for uid in self._user_ids:
                    pending = await dlq.list_pending(uid, limit=10)
                    for entry in pending:
                        if not await dlq.mark_retrying(entry.entry_id):
                            logger.info(
                                "DLQ entry %s exhausted, marked as exhausted",
                                entry.entry_id,
                            )
                        else:
                            logger.debug(
                                "DLQ entry %s marked for retry (attempt %d)",
                                entry.entry_id,
                                entry.attempt_count,
                            )
                            dispatched = await self._dispatch_dlq_entry(db, entry, factory)
                            if dispatched:
                                await dlq.mark_resolved(entry.entry_id)
                            else:
                                logger.warning(
                                    "DLQ entry %s dispatch failed for op=%s",
                                    entry.entry_id,
                                    entry.operation_type,
                                )
                    await db.commit()
        except Exception:
            logger.warning("DLQ retry tick failed", exc_info=True)

    async def _dispatch_dlq_entry(self, db, entry, factory) -> bool:
        """Dispatch a single DLQ entry based on its operation_type.

        Returns True if the operation was successfully re-executed.
        """
        op = entry.operation_type
        payload = entry.payload or {}

        try:
            if op == "background_task":
                run_id = payload.get("run_id")
                if not run_id:
                    logger.warning("DLQ background_task missing run_id: %s", entry.entry_id)
                    return False
                run = await db.get(TaskRun, run_id)
                if not run:
                    logger.warning("DLQ TaskRun not found: %s", run_id)
                    return False
                if run.status == "failed":
                    transition_run(run, "pending")
                    await db.flush()
                else:
                    logger.debug(
                        "DLQ background_task run %s already in status '%s' — skipping transition",
                        run_id,
                        run.status,
                    )
                return True

            if op == "failed_embedding":
                # Full re-embed requires looking up source record by record_id
                # to retrieve text — deferred to a dedicated embedding retry service.
                logger.info(
                    "DLQ failed_embedding entry %s — skipping (requires dedicated retry service)",
                    entry.entry_id,
                )
                return False

            if op == "perception_cycle":
                source = payload.get("source")
                if not source:
                    logger.warning("DLQ perception_cycle missing source: %s", entry.entry_id)
                    return False
                if not self._orchestrator:
                    logger.warning(
                        "DLQ perception_cycle requires orchestrator: %s",
                        entry.entry_id,
                    )
                    return False
                await self._orchestrator._bump_perception_for_sources(
                    [source], entry.user_id, entry.workspace_id
                )
                return True

            logger.warning(
                "DLQ unknown operation_type %r for entry %s",
                op,
                entry.entry_id,
            )
            return False

        except Exception:
            logger.warning(
                "DLQ dispatch failed for entry %s (op=%s)",
                entry.entry_id,
                op,
                exc_info=True,
            )
            return False
