"""Memory Service — episodic, semantic, preference, and behavioral memory.

Jarvis's product memory — long-term, structured, searchable, and scored.

Responsibilities:
- Extract candidate memories from interactions and events
- Score memory usefulness and stability
- Store with provenance and vector embeddings
- Provide retrieval API: semantic (Qdrant) with text fallback
- Expire or demote low-value memories
"""

import logging
from datetime import datetime, timezone

from sqlalchemy import select, update
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


def _compute_decayed_stability(current_stability: float, days_since_access: int) -> float:
    """Compute new stability score with time-based decay and access boost.

    Formula: min(1.0, max(0.0, current - 0.02 * days) + 0.1)
    - Decays by 0.02 per day since last access
    - Adds 0.1 boost for the current access
    - Clamped to [0.0, 1.0]
    """
    decayed = max(0.0, current_stability - 0.02 * days_since_access)
    return min(1.0, decayed + 0.1)


class MemoryService:
    """Manage Jarvis long-term memory."""

    def __init__(self, settings: Settings, db: AsyncSession, event_bus=None, vector_store=None):
        self._settings = settings
        self._db = db
        self._client = get_anthropic_client(settings)
        self._embedder = EmbeddingService(settings)
        self._event_bus = event_bus
        self._vector_store = vector_store

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

    async def extract_and_store(
        self,
        user_id: str,
        source_text: str,
        source_event_ids: list[str],
        entity_ids: list[str] | None = None,
        workspace_id: str = "",
    ) -> list[str]:
        """Extract memories from text and store them. Returns memory_ids."""
        extracted = await self._call_extraction(source_text)
        memory_ids = []
        new_facts: list[tuple[str, str]] = []  # (memory_id, fact_text)

        for mem_data in extracted.get("memories", []):
            fact_text = mem_data.get("fact_text", "")
            if not fact_text:
                continue

            is_dup = await self._is_duplicate(user_id, fact_text, workspace_id=workspace_id)
            if is_dup:
                continue

            embedding = await self._embedder.embed_text(fact_text)

            memory_id = f"mem_{ULID()}"
            memory = Memory(
                memory_id=memory_id,
                user_id=user_id,
                workspace_id=workspace_id,
                memory_type=mem_data.get("memory_type", "semantic"),
                scope=mem_data.get("scope", "general"),
                fact_text=fact_text,
                confidence=mem_data.get("confidence", 0.5),
                stability_score=0.0,
                source_event_ids=source_event_ids,
                provenance={"extraction_method": "claude_auto"},
                ttl_days=mem_data.get("ttl_days"),
                status="active",
                entity_ids=entity_ids,
            )
            self._db.add(memory)
            memory_ids.append(memory_id)
            new_facts.append((memory_id, fact_text))

            if self._vector_store and embedding:
                await self._vector_store.upsert(
                    "memories",
                    memory_id,
                    embedding,
                    self._build_memory_payload(
                        memory_type=mem_data.get("memory_type", "semantic"),
                        fact_text=fact_text,
                        user_id=user_id,
                        confidence=mem_data.get("confidence", 0.5),
                        entity_ids=entity_ids,
                        scope=mem_data.get("scope"),
                    ),
                    user_id,
                )

        if memory_ids:
            await self._db.flush()
            logger.info(
                "Extracted %d memories from %d events",
                len(memory_ids),
                len(source_event_ids),
            )

            # Defer contradiction checks to background (avoids N Claude calls per store)
            for mid, fact in new_facts:
                if self._event_bus:
                    try:
                        await self._event_bus.publish(
                            self._event_bus.event_stream(user_id),
                            "contradiction_check_requested",
                            {
                                "memory_id": mid,
                                "fact_text": fact,
                                "user_id": user_id,
                                "workspace_id": workspace_id,
                            },
                            user_id=user_id,
                        )
                    except Exception:
                        logger.debug(
                            "Deferred contradiction check publish failed for %s",
                            mid,
                            exc_info=True,
                        )

            for mid in memory_ids:
                await self._emit_event("memory.created", user_id, {"memory_id": mid})

        return memory_ids

    async def extract_preferences(
        self,
        user_id: str,
        source_text: str,
        source_event_ids: list[str],
        workspace_id: str = "",
    ) -> list[str]:
        """Extract user preferences from interactions. Returns memory_ids."""
        extracted = await self._call_preference_extraction(source_text)
        memory_ids = []

        for pref_data in extracted.get("preferences", []):
            fact_text = pref_data.get("fact_text", "")
            if not fact_text:
                continue

            is_dup = await self._is_duplicate(user_id, fact_text, workspace_id=workspace_id)
            if is_dup:
                continue

            embedding = await self._embedder.embed_text(fact_text)

            memory_id = f"mem_{ULID()}"
            memory = Memory(
                memory_id=memory_id,
                user_id=user_id,
                workspace_id=workspace_id,
                memory_type="preference",
                scope=pref_data.get("category", "general"),
                fact_text=fact_text,
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

            if self._vector_store and embedding:
                await self._vector_store.upsert(
                    "memories",
                    memory_id,
                    embedding,
                    self._build_memory_payload(
                        memory_type="preference",
                        fact_text=fact_text,
                        user_id=user_id,
                        confidence=pref_data.get("confidence", 0.5),
                        scope=pref_data.get("category"),
                        preference_strength=pref_data.get("strength"),
                    ),
                    user_id,
                )

        if memory_ids:
            await self._db.flush()
            logger.info("Extracted %d preferences", len(memory_ids))

        return memory_ids

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

        if self._vector_store and embedding:
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

        if self._vector_store and embedding:
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

        if self._vector_store and embedding:
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

        if self._vector_store and embedding:
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

        logger.info("Memory stored: %s type=%s '%s'", memory_id, memory_type, fact_text[:80])
        await self._emit_event("memory.created", user_id, {"memory_id": memory_id})
        return memory_id

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

    async def refresh_stability(self, memory_id: str, user_id: str) -> None:
        """Refresh memory stability with time-based decay + access boost.

        Decays stability by 0.02 per day since last access, then adds 0.1.
        This ensures unused memories gradually decay while accessed ones stay stable.
        """
        try:
            now = datetime.now(timezone.utc)

            # Fetch current memory to compute decay
            result = await self._db.execute(select(Memory).where(Memory.memory_id == memory_id))
            memory = result.scalar_one_or_none()
            if not memory:
                return

            last_access = memory.last_accessed_at or memory.created_at
            days_since = (now - last_access).days if last_access else 0
            new_stability = _compute_decayed_stability(memory.stability_score or 0.0, days_since)

            stmt = (
                update(Memory)
                .where(Memory.memory_id == memory_id)
                .values(
                    refresh_count=Memory.refresh_count + 1,
                    last_accessed_at=now,
                    stability_score=new_stability,
                )
            )
            await self._db.execute(stmt)
            await self._db.flush()
            await self._emit_event(
                "memory.updated",
                user_id,
                {"action": "stability_refresh", "memory_id": memory_id},
            )
        except Exception:
            try:
                await self._db.rollback()
            except Exception:
                pass
            logger.debug("Failed to refresh stability for %s", memory_id, exc_info=True)

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

    async def _call_extraction(self, source_text: str) -> dict:
        """Call Claude to extract memories from text."""
        try:
            response = await self._client.messages.create(
                model=self._settings.resolved_model,
                max_tokens=1024,
                system=MEMORY_EXTRACTION_PROMPT,
                messages=[{"role": "user", "content": source_text}],
            )
            from src.llm_utils import parse_llm_json

            return parse_llm_json(response.content[0].text)
        except Exception:
            logger.debug("Memory extraction returned non-JSON", exc_info=True)
            return {"memories": []}

    async def _call_preference_extraction(self, source_text: str) -> dict:
        """Call Claude to extract preferences from text."""
        try:
            response = await self._client.messages.create(
                model=self._settings.resolved_model,
                max_tokens=1024,
                system=PREFERENCE_EXTRACTION_PROMPT,
                messages=[{"role": "user", "content": source_text}],
            )
            from src.llm_utils import parse_llm_json

            return parse_llm_json(response.content[0].text)
        except Exception:
            logger.debug("Preference extraction returned non-JSON", exc_info=True)
            return {"preferences": []}

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

    async def _emit_event(self, event_type: str, user_id: str, payload: dict) -> None:
        """Publish a domain event (best-effort)."""
        if not self._event_bus:
            return
        try:
            stream = self._event_bus.agent_stream(user_id)
            await self._event_bus.publish(stream, event_type, payload, user_id)
        except Exception:
            logger.debug("Failed to emit %s event", event_type, exc_info=True)
