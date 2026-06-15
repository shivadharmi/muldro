"""Duplicate detection and memory consolidation."""

import logging

from sqlalchemy import select

from src.models.memory import Memory

logger = logging.getLogger(__name__)


class MemoryConsolidation:
    """Merge near-duplicate memories and detect duplicates before storage."""

    async def consolidate_memories(self, user_id: str, workspace_id: str = "") -> int:
        """Find and merge highly similar memories (>0.95 similarity).

        Uses Qdrant find_similar for O(n) comparisons instead of O(n^2).
        Keeps the memory with higher confidence, increments its stability_score,
        and marks the duplicate as 'merged'. Returns count of merged memories.
        """
        stmt = select(Memory).where(
            Memory.user_id == user_id,
            Memory.workspace_id == workspace_id,
            Memory.status == "active",
        )
        result = await self._db.execute(stmt)
        memories = result.scalars().all()

        if len(memories) < 2:
            return 0

        merged_count = 0
        merged_ids: set[str] = set()

        for mem in memories:
            if mem.memory_id in merged_ids:
                continue

            if not self._vector_store:
                break

            # Re-embed the fact_text (embeddings live in Qdrant now)
            embedding = await self._embedder.embed_text(mem.fact_text)
            if not embedding:
                continue

            similar = await self._vector_store.find_similar(
                "memories",
                embedding,
                user_id,
                threshold=0.95,
                limit=5,
            )

            for s in similar:
                dup_id = s.get("payload", {}).get("_original_id") or s["id"]
                if dup_id == mem.memory_id or dup_id in merged_ids:
                    continue

                # Find the duplicate Memory row
                dup_result = await self._db.execute(
                    select(Memory).where(
                        Memory.memory_id == dup_id,
                        Memory.status == "active",
                    )
                )
                dup_mem = dup_result.scalar_one_or_none()
                if not dup_mem:
                    continue

                # Keep the one with higher confidence
                if mem.confidence >= dup_mem.confidence:
                    keeper, duplicate = mem, dup_mem
                else:
                    keeper, duplicate = dup_mem, mem

                keeper.stability_score = min((keeper.stability_score or 0.0) + 0.1, 1.0)
                duplicate.status = "merged"
                merged_ids.add(duplicate.memory_id)
                merged_count += 1
                # Cascade delete from Qdrant
                if self._vector_store:
                    try:
                        await self._vector_store.delete("memories", duplicate.memory_id)
                    except Exception:
                        logger.debug(
                            "Qdrant cascade delete failed for merged %s",
                            duplicate.memory_id,
                            exc_info=True,
                        )

                score = s.get("score", 0.0)
                logger.info(
                    "Merged memory %s into %s (similarity=%.4f)",
                    duplicate.memory_id,
                    keeper.memory_id,
                    score,
                )

        if merged_count > 0:
            await self._db.flush()
            logger.info(
                "Consolidated %d memories for user %s",
                merged_count,
                user_id,
            )
            await self._emit_event(
                "memory.updated",
                user_id,
                {"action": "consolidation", "merged_count": merged_count},
            )

        return merged_count

    async def _is_duplicate(self, user_id: str, fact_text: str, workspace_id: str = "") -> bool:
        """Check if a substantially similar memory already exists.

        Uses Qdrant semantic similarity when available,
        falls back to exact text match.
        """
        # Check exact match first (fast)
        result = await self._db.execute(
            select(Memory.memory_id).where(
                Memory.user_id == user_id,
                Memory.workspace_id == workspace_id,
                Memory.status == "active",
                Memory.fact_text == fact_text,
            )
        )
        if result.scalar_one_or_none() is not None:
            return True

        # Check semantic similarity via Qdrant
        if self._vector_store:
            embedding = await self._embedder.embed_text(fact_text)
            if embedding:
                similar = await self._vector_store.find_similar(
                    "memories",
                    embedding,
                    user_id,
                    threshold=0.92,
                    limit=1,
                )
                if similar:
                    return True

        return False
