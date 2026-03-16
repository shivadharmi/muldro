"""Memory Service — episodic, semantic, preference, and behavioral memory.

Jarvis's product memory — long-term, structured, searchable, and scored.

Responsibilities:
- Extract candidate memories from interactions and events
- Score memory usefulness and stability
- Store with provenance and vector embeddings
- Provide retrieval API: semantic (pgvector) with text fallback
- Expire or demote low-value memories
"""

import asyncio
import json
import logging
from datetime import datetime, timezone

from sqlalchemy import case, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID

from src.config.settings import Settings, get_anthropic_client
from src.models.memory import Memory
from src.services.embedding_service import EmbeddingService

logger = logging.getLogger(__name__)

MEMORY_EXTRACTION_PROMPT = """\
You are Jarvis's memory extraction engine. Given text from an event or \
interaction, extract facts worth remembering long-term.

You MUST respond with valid JSON matching this schema:
{
  "memories": [
    {
      "memory_type": "episodic" | "semantic" | "preference" | "relationship" | "task_context",
      "scope": "general" | "planning" | "presentation",
      "fact_text": "Concise, standalone fact (should make sense without context)",
      "confidence": float 0.0-1.0,
      "ttl_days": int or null (null = permanent)
    }
  ]
}

Rules:
- Extract 0-5 memories per input (don't force it — only extract genuinely useful facts)
- episodic: specific events ("User met with Alice on March 13")
- semantic: general knowledge ("Alice is CFO at Acme Corp")
- preference: user habits ("User prefers concise briefings")
- relationship: people patterns ("User and Bob discuss fundraising weekly")
- task_context: active work ("Series B deck is being finalized")
- Set confidence high (>0.8) for explicit facts, lower for inferences
- Set ttl_days to null for permanent facts, 30-90 for task_context
- Each fact_text must be self-contained — no pronouns without antecedents
"""

PREFERENCE_EXTRACTION_PROMPT = """\
You are Jarvis's preference extraction engine. Given an interaction or \
user feedback, extract user preferences and behavioral patterns.

You MUST respond with valid JSON matching this schema:
{
  "preferences": [
    {
      "category": "communication" | "scheduling" | "briefing" | "workflow" | "general",
      "fact_text": "Concise preference statement",
      "confidence": float 0.0-1.0,
      "strength": "strong" | "moderate" | "weak"
    }
  ]
}

Rules:
- Only extract explicit or strongly implied preferences
- Communication: tone, length, format, channel preferences
- Scheduling: time preferences, meeting habits, availability patterns
- Briefing: detail level, priority topics, format preferences
- Workflow: approval speed, risk tolerance, delegation style
- Each preference must be a standalone, actionable statement
- Set confidence high (>0.8) for explicit statements, lower for inferences
"""


class MemoryService:
    """Manage Jarvis long-term memory."""

    def __init__(self, settings: Settings, db: AsyncSession):
        self._settings = settings
        self._db = db
        self._client = get_anthropic_client(settings)
        self._embedder = EmbeddingService(settings)

    async def extract_and_store(
        self,
        user_id: str,
        source_text: str,
        source_event_ids: list[str],
    ) -> list[str]:
        """Extract memories from text and store them. Returns memory_ids."""
        extracted = await self._call_extraction(source_text)
        memory_ids = []

        for mem_data in extracted.get("memories", []):
            fact_text = mem_data.get("fact_text", "")
            if not fact_text:
                continue

            is_dup = await self._is_duplicate(user_id, fact_text)
            if is_dup:
                continue

            embedding = await self._embedder.embed_text(fact_text)

            memory_id = f"mem_{ULID()}"
            memory = Memory(
                memory_id=memory_id,
                user_id=user_id,
                memory_type=mem_data.get("memory_type", "semantic"),
                scope=mem_data.get("scope", "general"),
                fact_text=fact_text,
                embedding=embedding,
                confidence=mem_data.get("confidence", 0.5),
                stability_score=0.0,
                source_event_ids=source_event_ids,
                provenance={"extraction_method": "claude_auto"},
                ttl_days=mem_data.get("ttl_days"),
                status="active",
            )
            self._db.add(memory)
            memory_ids.append(memory_id)

        if memory_ids:
            await self._db.flush()
            logger.info(
                "Extracted %d memories from %d events",
                len(memory_ids),
                len(source_event_ids),
            )

        return memory_ids

    async def extract_preferences(
        self,
        user_id: str,
        source_text: str,
        source_event_ids: list[str],
    ) -> list[str]:
        """Extract user preferences from interactions. Returns memory_ids."""
        extracted = await self._call_preference_extraction(source_text)
        memory_ids = []

        for pref_data in extracted.get("preferences", []):
            fact_text = pref_data.get("fact_text", "")
            if not fact_text:
                continue

            is_dup = await self._is_duplicate(user_id, fact_text)
            if is_dup:
                continue

            embedding = await self._embedder.embed_text(fact_text)

            memory_id = f"mem_{ULID()}"
            memory = Memory(
                memory_id=memory_id,
                user_id=user_id,
                memory_type="preference",
                scope=pref_data.get("category", "general"),
                fact_text=fact_text,
                embedding=embedding,
                confidence=pref_data.get("confidence", 0.5),
                stability_score=0.0,
                source_event_ids=source_event_ids,
                provenance={
                    "extraction_method": "claude_preference",
                    "strength": pref_data.get("strength", "moderate"),
                },
                ttl_days=None,
                status="active",
            )
            self._db.add(memory)
            memory_ids.append(memory_id)

        if memory_ids:
            await self._db.flush()
            logger.info("Extracted %d preferences", len(memory_ids))

        return memory_ids

    async def retrieve(
        self,
        user_id: str,
        query: str,
        memory_types: list[str] | None = None,
        entity_refs: list[str] | None = None,
        max_results: int = 10,
    ) -> list[dict]:
        """Retrieve relevant memories using semantic search with text fallback."""
        # Try semantic search first
        query_embedding = await self._embedder.embed_text(query)
        if query_embedding:
            results = await self._semantic_retrieve(
                user_id, query_embedding, memory_types, max_results
            )
        else:
            # Fall back to text-based ILIKE matching
            results = await self._text_retrieve(user_id, query, memory_types, max_results)

        # Fire-and-forget refresh stability for each retrieved memory
        for result in results:
            asyncio.create_task(self.refresh_stability(result["memory_id"]))

        return results

    async def get_user_preferences(
        self,
        user_id: str,
        category: str | None = None,
        max_results: int = 20,
    ) -> list[dict]:
        """Get user preferences, optionally filtered by category."""
        stmt = select(Memory).where(
            Memory.user_id == user_id,
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

    async def check_contradictions(
        self,
        user_id: str,
        new_fact: str,
        new_memory_id: str,
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

        sql = text("""
            SELECT memory_id, fact_text
            FROM memories
            WHERE user_id = :user_id
              AND status = 'active'
              AND memory_id != :new_id
              AND embedding IS NOT NULL
              AND 1 - (embedding <=> cast(:embedding as vector)) > 0.7
            LIMIT 10
        """)
        result = await self._db.execute(
            sql, {"user_id": user_id, "new_id": new_memory_id, "embedding": str(embedding)}
        )
        candidates = result.all()

        if not candidates:
            return superseded

        # Ask Claude to check for contradictions
        for row in candidates:
            is_contradiction = await self._check_contradiction_pair(new_fact, row.fact_text)
            if is_contradiction:
                # Supersede the old memory
                stmt = (
                    update(Memory)
                    .where(Memory.memory_id == row.memory_id)
                    .values(
                        superseded_by=new_memory_id,
                        confidence=Memory.confidence * 0.5,
                    )
                )
                await self._db.execute(stmt)
                superseded.append(row.memory_id)
                logger.info(
                    "Memory %s superseded by %s (contradiction)",
                    row.memory_id,
                    new_memory_id,
                )

        if superseded:
            await self._db.flush()

        return superseded

    async def _check_contradiction_pair(self, fact_a: str, fact_b: str) -> bool:
        """Check if two facts contradict each other using Claude."""
        try:
            response = await self._client.messages.create(
                model=self._settings.anthropic_model,
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
            result_text = response.content[0].text
            if result_text.startswith("```"):
                result_text = result_text.split("\n", 1)[1].rsplit("```", 1)[0]
            return json.loads(result_text).get("contradicts", False)
        except Exception:
            logger.debug("Contradiction check failed", exc_info=True)
            return False

    async def consolidate_memories(self, user_id: str) -> int:
        """Find and merge highly similar memories (>0.95 similarity).

        Keeps the memory with higher confidence, increments its stability_score,
        and marks the duplicate as 'merged'. Returns count of merged memories.
        """
        # Find all active memories with embeddings for this user
        stmt = select(Memory).where(
            Memory.user_id == user_id,
            Memory.status == "active",
            Memory.embedding.isnot(None),
        )
        result = await self._db.execute(stmt)
        memories = result.scalars().all()

        if len(memories) < 2:
            return 0

        merged_count = 0

        # Compare each pair of memories
        for i, mem1 in enumerate(memories):
            if mem1.status != "active":  # May have been marked merged in previous iteration
                continue

            for mem2 in memories[i + 1 :]:
                if mem2.status != "active":
                    continue

                # Calculate similarity
                sql = text("""
                    SELECT 1 - (
                        cast(:embedding1 as vector) <=> cast(:embedding2 as vector)
                    ) AS similarity
                """)
                sim_result = await self._db.execute(
                    sql, {"embedding1": str(mem1.embedding), "embedding2": str(mem2.embedding)}
                )
                similarity = sim_result.scalar_one()

                # If very high similarity, merge them
                if similarity > 0.95:
                    # Keep the one with higher confidence
                    if mem1.confidence >= mem2.confidence:
                        keeper, duplicate = mem1, mem2
                    else:
                        keeper, duplicate = mem2, mem1

                    # Update keeper: increment stability
                    keeper.stability_score = min(keeper.stability_score + 0.1, 1.0)

                    # Mark duplicate as merged
                    duplicate.status = "merged"

                    merged_count += 1
                    logger.info(
                        "Merged memory %s into %s (similarity=%.4f)",
                        duplicate.memory_id,
                        keeper.memory_id,
                        similarity,
                    )

        if merged_count > 0:
            await self._db.flush()
            logger.info("Consolidated %d memories for user %s", merged_count, user_id)

        return merged_count

    async def refresh_stability(self, memory_id: str) -> None:
        """Refresh memory stability when accessed.

        Increments refresh_count, updates last_accessed_at, and increases
        stability_score by 0.1 (capped at 1.0).
        """
        try:
            stmt = (
                update(Memory)
                .where(Memory.memory_id == memory_id)
                .values(
                    refresh_count=Memory.refresh_count + 1,
                    last_accessed_at=datetime.now(timezone.utc),
                    stability_score=case(
                        (Memory.stability_score + 0.1 < 1.0, Memory.stability_score + 0.1),
                        else_=1.0,
                    ),
                )
            )
            await self._db.execute(stmt)
            await self._db.flush()
        except Exception:
            # Fire-and-forget, don't let refresh failure block retrieval
            logger.debug("Failed to refresh stability for %s", memory_id, exc_info=True)

    async def _semantic_retrieve(
        self,
        user_id: str,
        query_embedding: list[float],
        memory_types: list[str] | None,
        max_results: int,
    ) -> list[dict]:
        """Retrieve memories using pgvector cosine similarity with recency boost."""
        type_filter = ""
        params: dict = {
            "user_id": user_id,
            "embedding": str(query_embedding),
            "limit": max_results,
        }

        if memory_types:
            type_filter = "AND memory_type = ANY(:memory_types)"
            params["memory_types"] = memory_types

        # Add recency weighting: boost memories accessed in last 7 days by 0.05
        sql = text(f"""
            SELECT memory_id, memory_type, fact_text, confidence, scope,
                   1 - (embedding <=> cast(:embedding as vector)) AS similarity,
                   CASE
                       WHEN last_accessed_at IS NOT NULL
                       AND last_accessed_at > NOW() - INTERVAL '7 days'
                       THEN 0.05
                       ELSE 0.0
                   END AS recency_boost
            FROM memories
            WHERE user_id = :user_id
              AND status = 'active'
              AND embedding IS NOT NULL
              {type_filter}
            ORDER BY (1 - (embedding <=> cast(:embedding as vector))) +
                     CASE
                         WHEN last_accessed_at IS NOT NULL
                         AND last_accessed_at > NOW() - INTERVAL '7 days'
                         THEN 0.05
                         ELSE 0.0
                     END DESC
            LIMIT :limit
        """)

        result = await self._db.execute(sql, params)
        rows = result.all()

        return [
            {
                "memory_id": row.memory_id,
                "memory_type": row.memory_type,
                "fact_text": row.fact_text,
                "confidence": row.confidence,
                "scope": row.scope,
                "similarity": round(row.similarity, 4) if row.similarity else None,
            }
            for row in rows
        ]

    async def _text_retrieve(
        self,
        user_id: str,
        query: str,
        memory_types: list[str] | None,
        max_results: int,
    ) -> list[dict]:
        """Fallback text-based ILIKE retrieval."""
        stmt = select(Memory).where(
            Memory.user_id == user_id,
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

    async def _call_extraction(self, source_text: str) -> dict:
        """Call Claude to extract memories from text."""
        try:
            response = await self._client.messages.create(
                model=self._settings.anthropic_model,
                max_tokens=1024,
                system=MEMORY_EXTRACTION_PROMPT,
                messages=[{"role": "user", "content": source_text}],
            )
            text = response.content[0].text
            if text.startswith("```"):
                text = text.split("\n", 1)[1].rsplit("```", 1)[0]
            return json.loads(text)
        except Exception:
            logger.warning("Memory extraction failed", exc_info=True)
            return {"memories": []}

    async def _call_preference_extraction(self, source_text: str) -> dict:
        """Call Claude to extract preferences from text."""
        try:
            response = await self._client.messages.create(
                model=self._settings.anthropic_model,
                max_tokens=1024,
                system=PREFERENCE_EXTRACTION_PROMPT,
                messages=[{"role": "user", "content": source_text}],
            )
            text = response.content[0].text
            if text.startswith("```"):
                text = text.split("\n", 1)[1].rsplit("```", 1)[0]
            return json.loads(text)
        except Exception:
            logger.warning("Preference extraction failed", exc_info=True)
            return {"preferences": []}

    async def _is_duplicate(self, user_id: str, fact_text: str) -> bool:
        """Check if a substantially similar memory already exists.

        Uses semantic similarity when embeddings are available,
        falls back to exact text match.
        """
        # Check exact match first (fast)
        result = await self._db.execute(
            select(Memory.memory_id).where(
                Memory.user_id == user_id,
                Memory.status == "active",
                Memory.fact_text == fact_text,
            )
        )
        if result.scalar_one_or_none() is not None:
            return True

        # Check semantic similarity for near-duplicates
        embedding = await self._embedder.embed_text(fact_text)
        if embedding:
            sql = text("""
                SELECT memory_id
                FROM memories
                WHERE user_id = :user_id
                  AND status = 'active'
                  AND embedding IS NOT NULL
                  AND 1 - (embedding <=> cast(:embedding as vector)) > 0.92
                LIMIT 1
            """)
            result = await self._db.execute(sql, {"user_id": user_id, "embedding": str(embedding)})
            if result.scalar_one_or_none() is not None:
                return True

        return False
