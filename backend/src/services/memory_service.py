"""Memory Service — episodic, semantic, preference, and behavioral memory.

This is NOT OpenClaw's session memory. This is Jarvis's product memory —
long-term, structured, searchable, and scored.

Responsibilities:
- Extract candidate memories from interactions and events
- Score memory usefulness and stability
- Store with provenance and vector embeddings
- Provide retrieval API: semantic (pgvector) with text fallback
- Expire or demote low-value memories
"""

import json
import logging

from sqlalchemy import select, text
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
            return await self._semantic_retrieve(
                user_id, query_embedding, memory_types, max_results
            )

        # Fall back to text-based ILIKE matching
        return await self._text_retrieve(user_id, query, memory_types, max_results)

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

    async def _semantic_retrieve(
        self,
        user_id: str,
        query_embedding: list[float],
        memory_types: list[str] | None,
        max_results: int,
    ) -> list[dict]:
        """Retrieve memories using pgvector cosine similarity."""
        type_filter = ""
        params: dict = {
            "user_id": user_id,
            "embedding": str(query_embedding),
            "limit": max_results,
        }

        if memory_types:
            type_filter = "AND memory_type = ANY(:memory_types)"
            params["memory_types"] = memory_types

        sql = text(f"""
            SELECT memory_id, memory_type, fact_text, confidence, scope,
                   1 - (embedding <=> cast(:embedding as vector)) AS similarity
            FROM memories
            WHERE user_id = :user_id
              AND status = 'active'
              AND embedding IS NOT NULL
              {type_filter}
            ORDER BY embedding <=> cast(:embedding as vector)
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
