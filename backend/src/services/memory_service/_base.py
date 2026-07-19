"""Base collaborators and shared low-level helpers for MemoryService."""

import logging
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from src.config.settings import Settings
from src.services.embedding_service import EmbeddingService

logger = logging.getLogger(__name__)


class MemoryServiceBase:
    """Injected collaborators (__init__) and shared low-level DLQ/payload/event helpers."""

    def __init__(
        self,
        settings: Settings,
        db: AsyncSession,
        event_bus=None,
        vector_store=None,
        dead_letter=None,
    ):
        self._settings = settings
        self._db = db
        self._embedder = EmbeddingService(settings)
        self._event_bus = event_bus
        self._vector_store = vector_store
        self._dead_letter = dead_letter

    async def _enqueue_failed_embedding(
        self, record_id: str, user_id: str, collection: str = "memories"
    ) -> None:
        """Enqueue a failed embedding for retry via DLQ."""
        if not self._dead_letter:
            return
        try:
            await self._dead_letter.enqueue(
                user_id=user_id,
                operation_type="failed_embedding",
                error_type="EmbeddingFailure",
                error_message=f"Embedding/upsert failed for {collection}:{record_id}",
                payload={
                    "record_id": record_id,
                    "collection": collection,
                    "record_type": "memory",
                },
            )
        except Exception:
            logger.warning(
                "Failed to enqueue embedding retry for %s",
                record_id,
                exc_info=True,
            )

    @staticmethod
    def _build_memory_payload(
        memory_type: str,
        fact_text: str,
        user_id: str,
        confidence: float = 0.5,
        stability_score: float = 0.0,
        entity_ids: list[str] | None = None,
        scope: str | None = None,
        preference_strength: str | None = None,
    ) -> dict:
        """Build enriched Qdrant payload for a memory."""
        return {
            "memory_type": memory_type,
            "fact_text": fact_text,
            "user_id": user_id,
            "confidence": confidence,
            "stability_score": stability_score,
            "entity_ids": entity_ids or [],
            "scope": scope or "general",
            "preference_strength": preference_strength,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

    async def _emit_event(
        self, event_type: str, user_id: str, payload: dict, workspace_id: str = ""
    ) -> None:
        """Publish a domain event (best-effort)."""
        if not self._event_bus:
            return
        try:
            stream = self._event_bus.agent_stream(workspace_id)
            await self._event_bus.publish(
                stream, event_type, payload, user_id, workspace_id=workspace_id
            )
        except Exception:
            logger.debug("Failed to emit %s event", event_type, exc_info=True)
