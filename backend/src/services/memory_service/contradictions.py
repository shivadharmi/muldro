"""Contradiction detection between stored memories."""

import logging

from sqlalchemy import update

from src.models.memory import Memory

logger = logging.getLogger(__name__)


class MemoryContradictions:
    """Find and supersede memories that contradict a newly stored fact."""

    async def check_contradictions(
        self,
        user_id: str,
        new_fact: str,
        new_memory_id: str,
        workspace_id: str = "",
    ) -> list[str]:
        """Check if a new memory contradicts existing ones.

        If contradiction found: set old memory superseded_by = new_memory_id,
        lower old confidence. Returns list of superseded memory_ids.
        """
        superseded = []
        # Find similar memories that might contradict
        embedding = await self._embedder.embed_text(new_fact)
        if not embedding:
            return superseded

        candidates = []
        if self._vector_store:
            similar = await self._vector_store.find_similar(
                "memories",
                embedding,
                user_id,
                threshold=0.7,
                limit=10,
            )
            candidates = [
                (
                    s.get("payload", {}).get("_original_id") or s["id"],
                    s.get("payload", {}).get("fact_text", ""),
                )
                for s in similar
                if (s.get("payload", {}).get("_original_id") or s["id"]) != new_memory_id
            ]

        if not candidates:
            return superseded

        # Ask Claude to check for contradictions
        for cand_id, cand_text in candidates:
            is_contradiction = await self._check_contradiction_pair(new_fact, cand_text)
            if is_contradiction:
                # Supersede the old memory
                stmt = (
                    update(Memory)
                    .where(Memory.memory_id == cand_id)
                    .values(
                        superseded_by=new_memory_id,
                        confidence=Memory.confidence * 0.5,
                    )
                )
                await self._db.execute(stmt)
                superseded.append(cand_id)
                logger.info(
                    "Memory %s superseded by %s (contradiction)",
                    cand_id,
                    new_memory_id,
                )
                # Cascade delete from Qdrant
                if self._vector_store:
                    try:
                        await self._vector_store.delete("memories", cand_id)
                    except Exception:
                        logger.debug(
                            "Qdrant cascade delete failed for superseded %s",
                            cand_id,
                            exc_info=True,
                        )

        if superseded:
            await self._db.flush()
            for old_id in superseded:
                await self._emit_event(
                    "memory.updated",
                    user_id,
                    {"memory_id": old_id, "superseded_by": new_memory_id},
                )

        return superseded

    async def _check_contradiction_pair(self, fact_a: str, fact_b: str) -> bool:
        """Check if two facts contradict each other using Claude."""
        try:
            response = await self._client.messages.create(
                model=self._settings.resolved_model,
                max_tokens=64,
                system=(
                    "You check if two facts contradict each other. "
                    'Respond with JSON: {"contradicts": true/false}'
                ),
                messages=[
                    {
                        "role": "user",
                        "content": f"Fact A: {fact_a}\nFact B: {fact_b}",
                    }
                ],
            )
            from src.llm_utils import parse_llm_json

            return parse_llm_json(response.content[0].text).get("contradicts", False)
        except Exception:
            logger.debug("Contradiction check failed", exc_info=True)
            return False
