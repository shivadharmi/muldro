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
                return await self._retry_failed_embedding(db, entry)

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

    async def _retry_failed_embedding(self, db, entry) -> bool:
        """Re-embed a record whose original Qdrant upsert failed.

        Looks up the source record (Memory or Entity) by ``record_id``,
        re-embeds its text via the embedding service, and upserts the vector
        to Qdrant. Returns True only on a successful upsert.
        """
        payload = entry.payload or {}
        record_id = payload.get("record_id")
        collection = payload.get("collection")
        record_type = payload.get("record_type")
        if not record_id or not collection:
            logger.warning("DLQ failed_embedding missing record_id/collection: %s", entry.entry_id)
            return False
        if not getattr(self._settings, "qdrant_url", None):
            logger.info(
                "DLQ failed_embedding entry %s — Qdrant not configured, skipping",
                entry.entry_id,
            )
            return False

        text, vector_payload = await self._load_embedding_source(db, record_type, record_id)
        if not text:
            logger.warning(
                "DLQ failed_embedding source record %s not found or empty: %s",
                record_id,
                entry.entry_id,
            )
            return False

        from src.services.embedding_service import EmbeddingService
        from src.services.vector_store import VectorStore

        embedding = await EmbeddingService(self._settings).embed_text(text)
        if not embedding:
            logger.warning(
                "DLQ failed_embedding re-embed produced no vector for %s: %s",
                record_id,
                entry.entry_id,
            )
            return False

        await VectorStore(self._settings).upsert(
            collection, record_id, embedding, vector_payload, entry.user_id
        )
        logger.info("DLQ failed_embedding re-embedded %s:%s", collection, record_id)
        return True

    @staticmethod
    async def _load_embedding_source(
        db, record_type: str | None, record_id: str
    ) -> tuple[str | None, dict]:
        """Resolve the (text, Qdrant payload) for a failed-embedding record.

        Mirrors the payloads built at the original write sites
        (memory_service.storage / world_model). Returns ``(None, {})`` when
        the record no longer exists.
        """
        if record_type == "memory":
            from src.models.memory import Memory

            rec = await db.get(Memory, record_id)
            if not rec or not rec.fact_text:
                return None, {}
            return rec.fact_text, {
                "memory_type": rec.memory_type,
                "fact_text": rec.fact_text,
                "user_id": rec.user_id,
                "confidence": rec.confidence,
                "stability_score": rec.stability_score,
                "entity_ids": rec.entity_ids or [],
                "scope": rec.scope or "general",
            }
        if record_type == "entity":
            from src.models.entities import Entity

            rec = await db.get(Entity, record_id)
            if not rec or not rec.canonical_name:
                return None, {}
            return rec.canonical_name, {
                "entity_type": rec.entity_type,
                "canonical_name": rec.canonical_name,
                "user_id": rec.user_id,
            }
        logger.warning("DLQ failed_embedding unknown record_type %r", record_type)
        return None, {}
