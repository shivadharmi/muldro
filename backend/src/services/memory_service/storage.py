"""Typed and generic memory storage operations."""

import logging

from ulid import ULID

from src.models.memory import Memory

logger = logging.getLogger(__name__)


class MemoryStorage:
    """Store goals, instructions, briefing items, and generic memories."""

    async def store_goal_memory(
        self,
        user_id: str,
        workspace_id: str,
        title: str,
        description: str | None = None,
        target_date: str | None = None,
        priority: str = "medium",
        entity_ids: list[str] | None = None,
    ) -> str:
        """Store a goal as a memory with memory_type='goal'.

        Returns the memory_id.
        """
        parts = [f"Goal: {title}"]
        if description:
            parts.append(description)
        if target_date:
            parts.append(f"Target date: {target_date}")
        parts.append(f"Priority: {priority}")
        fact_text = ". ".join(parts)

        embedding = await self._embedder.embed_text(fact_text)
        memory_id = f"mem_{ULID()}"
        memory = Memory(
            memory_id=memory_id,
            user_id=user_id,
            workspace_id=workspace_id,
            memory_type="goal",
            scope="planning",
            fact_text=fact_text,
            confidence=0.9,
            stability_score=0.5,
            source_event_ids=[],
            provenance={"source": "user_goal", "priority": priority},
            ttl_days=None,
            status="active",
            entity_ids=entity_ids,
        )
        self._db.add(memory)
        await self._db.flush()

        if embedding:
            if self._vector_store:
                try:
                    await self._vector_store.upsert(
                        "memories",
                        memory_id,
                        embedding,
                        self._build_memory_payload(
                            memory_type="goal",
                            fact_text=fact_text,
                            user_id=user_id,
                            confidence=0.9,
                            stability_score=0.5,
                            entity_ids=entity_ids,
                            scope="planning",
                        ),
                        user_id,
                    )
                except Exception:
                    logger.debug("Qdrant upsert failed for %s", memory_id, exc_info=True)
                    await self._enqueue_failed_embedding(memory_id, user_id)
            else:
                await self._enqueue_failed_embedding(memory_id, user_id)
        else:
            await self._enqueue_failed_embedding(memory_id, user_id)

        logger.info("Goal memory stored: %s '%s'", memory_id, title)
        return memory_id

    async def store_instruction_memory(
        self,
        user_id: str,
        workspace_id: str,
        instruction_text: str,
        instruction_type: str = "preference",
    ) -> str:
        """Store a user instruction as a preference memory.

        Returns the memory_id.
        """
        fact_text = f"Instruction: {instruction_text}"
        embedding = await self._embedder.embed_text(fact_text)
        memory_id = f"mem_{ULID()}"
        memory = Memory(
            memory_id=memory_id,
            user_id=user_id,
            workspace_id=workspace_id,
            memory_type="preference",
            scope="general",
            fact_text=fact_text,
            confidence=0.95,
            stability_score=0.8,
            source_event_ids=[],
            provenance={
                "source": "user_instruction",
                "instruction_type": instruction_type,
            },
            ttl_days=None,
            status="active",
        )
        self._db.add(memory)
        await self._db.flush()

        if embedding:
            if self._vector_store:
                try:
                    await self._vector_store.upsert(
                        "memories",
                        memory_id,
                        embedding,
                        self._build_memory_payload(
                            memory_type="preference",
                            fact_text=fact_text,
                            user_id=user_id,
                            confidence=0.95,
                            stability_score=0.8,
                            scope="general",
                        ),
                        user_id,
                    )
                except Exception:
                    logger.debug("Qdrant upsert failed for %s", memory_id, exc_info=True)
                    await self._enqueue_failed_embedding(memory_id, user_id)
            else:
                await self._enqueue_failed_embedding(memory_id, user_id)
        else:
            await self._enqueue_failed_embedding(memory_id, user_id)

        logger.info(
            "Instruction memory stored: %s '%s'",
            memory_id,
            instruction_text[:80],
        )
        return memory_id

    async def store_briefing_memory(
        self,
        user_id: str,
        workspace_id: str,
        text: str,
        source: str = "perception",
        relevance_score: float | None = None,
        signal_source: str | None = None,
    ) -> str:
        """Store a briefing item as a short-lived memory (24h TTL).

        Briefing items are surfaced in the next daily briefing and then expire.
        Returns the memory_id.
        """
        embedding = await self._embedder.embed_text(text)
        memory_id = f"mem_{ULID()}"
        memory = Memory(
            memory_id=memory_id,
            user_id=user_id,
            workspace_id=workspace_id,
            memory_type="briefing_item",
            scope="planning",
            fact_text=text,
            confidence=0.8,
            stability_score=0.3,
            source_event_ids=[],
            provenance={
                "source": source,
                **({"relevance_score": relevance_score} if relevance_score is not None else {}),
                **({"signal_source": signal_source} if signal_source is not None else {}),
            },
            ttl_days=1,
            status="active",
        )
        self._db.add(memory)
        await self._db.flush()

        if embedding:
            if self._vector_store:
                try:
                    await self._vector_store.upsert(
                        "memories",
                        memory_id,
                        embedding,
                        self._build_memory_payload(
                            memory_type="briefing_item",
                            fact_text=text,
                            user_id=user_id,
                            confidence=0.8,
                            stability_score=0.3,
                            scope="planning",
                        ),
                        user_id,
                    )
                except Exception:
                    logger.debug("Qdrant upsert failed for %s", memory_id, exc_info=True)
                    await self._enqueue_failed_embedding(memory_id, user_id)
            else:
                await self._enqueue_failed_embedding(memory_id, user_id)
        else:
            await self._enqueue_failed_embedding(memory_id, user_id)

        logger.info("Briefing memory stored: %s '%s'", memory_id, text[:80])
        return memory_id

    async def store_memory(
        self,
        user_id: str,
        fact_text: str,
        memory_type: str = "fact",
        scope: str = "general",
        entity_ids: list[str] | None = None,
        workspace_id: str = "",
        ttl_days: int | None = None,
        source: str = "agent",
    ) -> str:
        """Store a single memory directly (no Claude extraction).

        Returns the memory_id.
        """
        embedding = await self._embedder.embed_text(fact_text)
        memory_id = f"mem_{ULID()}"
        memory = Memory(
            memory_id=memory_id,
            user_id=user_id,
            workspace_id=workspace_id,
            memory_type=memory_type,
            scope=scope,
            fact_text=fact_text,
            confidence=0.8,
            stability_score=0.0,
            source_event_ids=[],
            provenance={"source": source, "extraction_method": "direct"},
            ttl_days=ttl_days,
            status="active",
            entity_ids=entity_ids,
        )
        self._db.add(memory)
        await self._db.flush()

        if embedding:
            if self._vector_store:
                try:
                    await self._vector_store.upsert(
                        "memories",
                        memory_id,
                        embedding,
                        self._build_memory_payload(
                            memory_type=memory_type,
                            fact_text=fact_text,
                            user_id=user_id,
                            confidence=0.8,
                            entity_ids=entity_ids,
                            scope=scope,
                        ),
                        user_id,
                    )
                except Exception:
                    logger.debug("Qdrant upsert failed for %s", memory_id, exc_info=True)
                    await self._enqueue_failed_embedding(memory_id, user_id)
            else:
                await self._enqueue_failed_embedding(memory_id, user_id)
        else:
            await self._enqueue_failed_embedding(memory_id, user_id)

        logger.info("Memory stored: %s type=%s '%s'", memory_id, memory_type, fact_text[:80])
        await self._emit_event("memory.created", user_id, {"memory_id": memory_id})
        return memory_id
