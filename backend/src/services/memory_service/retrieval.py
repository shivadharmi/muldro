"""Memory retrieval and composite ranking."""

import logging
from datetime import datetime, timezone

from sqlalchemy import select

from src.models.memory import Memory

logger = logging.getLogger(__name__)


class MemoryRetrieval:
    """Composite semantic retrieval with text fallback, plus preference queries."""

    async def retrieve(
        self,
        user_id: str,
        query: str,
        memory_types: list[str] | None = None,
        entity_refs: list[str] | None = None,
        max_results: int = 10,
        workspace_id: str = "",
    ) -> list[dict]:
        """Retrieve relevant memories using composite ranking with text fallback.

        Ranking formula:
          0.40 * relevance (cosine similarity)
        + 0.25 * recency (decay over 30 days)
        + 0.15 * confidence
        + 0.10 * stability
        + 0.10 * entity_overlap (bonus if memory shares entities with query)
        """
        query_embedding = await self._embedder.embed_text(query)
        if query_embedding:
            results = await self._composite_retrieve(
                user_id,
                query_embedding,
                memory_types,
                entity_refs,
                max_results,
                workspace_id=workspace_id,
            )
        else:
            results = await self._text_retrieve(
                user_id,
                query,
                memory_types,
                max_results,
                workspace_id=workspace_id,
            )

        # Update stability scores sequentially — asyncio.create_task on a shared
        # DB session causes concurrent query errors that poison the transaction.
        for result in results:
            await self.refresh_stability(result["memory_id"], user_id=user_id)

        return results

    async def get_user_preferences(
        self,
        user_id: str,
        category: str | None = None,
        max_results: int = 20,
        workspace_id: str = "",
    ) -> list[dict]:
        """Get user preferences, optionally filtered by category."""
        stmt = select(Memory).where(
            Memory.user_id == user_id,
            Memory.workspace_id == workspace_id,
            Memory.status == "active",
            Memory.memory_type == "preference",
        )

        if category:
            stmt = stmt.where(Memory.scope == category)

        stmt = stmt.order_by(Memory.confidence.desc()).limit(max_results)

        result = await self._db.execute(stmt)
        memories = result.scalars().all()

        return [
            {
                "memory_id": m.memory_id,
                "category": m.scope,
                "fact_text": m.fact_text,
                "confidence": m.confidence,
                "strength": (m.provenance or {}).get("strength", "moderate"),
            }
            for m in memories
        ]

    async def _composite_retrieve(
        self,
        user_id: str,
        query_embedding: list[float],
        memory_types: list[str] | None,
        entity_refs: list[str] | None,
        max_results: int,
        workspace_id: str = "",
    ) -> list[dict]:
        """Retrieve memories using Qdrant + Postgres composite ranking.

        Score = 0.40*relevance + 0.25*recency + 0.15*confidence
              + 0.10*stability + 0.10*entity_overlap
        """
        if not self._vector_store:
            return await self._text_retrieve(
                user_id,
                "",
                memory_types,
                max_results,
                workspace_id=workspace_id,
            )

        # Step 1: Qdrant semantic search
        qdrant_filters = {}
        if workspace_id:
            qdrant_filters["workspace_id"] = workspace_id
        qdrant_results = await self._vector_store.search(
            "memories",
            query_embedding,
            user_id,
            filters=qdrant_filters if qdrant_filters else None,
            limit=max_results * 2,
        )
        if not qdrant_results:
            return []

        # Step 2: Extract memory_ids and batch-fetch from Postgres
        memory_ids = [r.get("payload", {}).get("_original_id") or r["id"] for r in qdrant_results]
        stmt = select(Memory).where(
            Memory.memory_id.in_(memory_ids),
            Memory.status == "active",
            Memory.workspace_id == workspace_id,
        )
        if memory_types:
            stmt = stmt.where(Memory.memory_type.in_(memory_types))

        result = await self._db.execute(stmt)
        rows = result.scalars().all()
        memory_map = {m.memory_id: m for m in rows}

        # Step 3: Composite scoring
        now = datetime.now(timezone.utc)
        scored = []
        for r in qdrant_results:
            mem_id = r.get("payload", {}).get("_original_id") or r["id"]
            mem = memory_map.get(mem_id)
            if not mem:
                continue

            relevance = r.get("score", 0.5)
            accessed = mem.last_accessed_at or mem.created_at
            age_seconds = (now - accessed).total_seconds()
            recency = max(0.0, 1.0 - age_seconds / (30 * 86400))
            confidence = mem.confidence or 0.5
            stability = mem.stability_score or 0.0
            entity_overlap = (
                1.0
                if (entity_refs and mem.entity_ids and set(entity_refs) & set(mem.entity_ids))
                else 0.0
            )
            score = (
                0.40 * relevance
                + 0.25 * recency
                + 0.15 * confidence
                + 0.10 * stability
                + 0.10 * entity_overlap
            )
            scored.append((score, relevance, mem))

        scored.sort(key=lambda x: x[0], reverse=True)

        return [
            {
                "memory_id": mem.memory_id,
                "memory_type": mem.memory_type,
                "fact_text": mem.fact_text,
                "confidence": mem.confidence,
                "scope": mem.scope,
                "relevance": round(rel, 4),
                "entity_ids": mem.entity_ids,
            }
            for _, rel, mem in scored[:max_results]
        ]

    async def _text_retrieve(
        self,
        user_id: str,
        query: str,
        memory_types: list[str] | None,
        max_results: int,
        workspace_id: str = "",
    ) -> list[dict]:
        """Fallback text-based ILIKE retrieval."""
        stmt = select(Memory).where(
            Memory.user_id == user_id,
            Memory.workspace_id == workspace_id,
            Memory.status == "active",
            Memory.fact_text.ilike(f"%{query}%"),
        )

        if memory_types:
            stmt = stmt.where(Memory.memory_type.in_(memory_types))

        stmt = stmt.order_by(Memory.confidence.desc()).limit(max_results)

        result = await self._db.execute(stmt)
        memories = result.scalars().all()

        return [
            {
                "memory_id": m.memory_id,
                "memory_type": m.memory_type,
                "fact_text": m.fact_text,
                "confidence": m.confidence,
                "scope": m.scope,
            }
            for m in memories
        ]
