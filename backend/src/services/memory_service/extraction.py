"""Memory and preference extraction from text via the LLM."""

import logging

from ulid import ULID

from src.llm.utility import complete_text_with_usage
from src.models.memory import Memory
from src.orchestrator.budget import record_token_span

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


class MemoryExtraction:
    """LLM-driven extraction of memories and preferences from interaction text"""

    async def extract_and_store(
        self,
        user_id: str,
        source_text: str,
        source_event_ids: list[str],
        entity_ids: list[str] | None = None,
        workspace_id: str = "",
        prompt_addendum: str | None = None,
        provenance_extra: dict | None = None,
    ) -> list[str]:
        """Extract memories from text and store them. Returns memory_ids."""
        extracted = await self._call_extraction(
            source_text, prompt_addendum=prompt_addendum, workspace_id=workspace_id
        )
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
                provenance={"extraction_method": "claude_auto", **(provenance_extra or {})},
                ttl_days=mem_data.get("ttl_days"),
                status="active",
                entity_ids=entity_ids,
            )
            self._db.add(memory)
            memory_ids.append(memory_id)
            new_facts.append((memory_id, fact_text))

            if embedding:
                if self._vector_store:
                    try:
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
                    except Exception:
                        logger.debug("Qdrant upsert failed for %s", memory_id, exc_info=True)
                        await self._enqueue_failed_embedding(memory_id, user_id)
                else:
                    await self._enqueue_failed_embedding(memory_id, user_id)
            else:
                await self._enqueue_failed_embedding(memory_id, user_id)

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
                            self._event_bus.event_stream(workspace_id),
                            "contradiction_check_requested",
                            {
                                "memory_id": mid,
                                "fact_text": fact,
                                "user_id": user_id,
                                "workspace_id": workspace_id,
                            },
                            user_id=user_id,
                            workspace_id=workspace_id,
                        )
                    except Exception:
                        logger.debug(
                            "Deferred contradiction check publish failed for %s",
                            mid,
                            exc_info=True,
                        )

            for mid in memory_ids:
                await self._emit_event(
                    "memory.created", user_id, {"memory_id": mid}, workspace_id=workspace_id
                )

        return memory_ids

    async def extract_preferences(
        self,
        user_id: str,
        source_text: str,
        source_event_ids: list[str],
        workspace_id: str = "",
    ) -> list[str]:
        """Extract user preferences from interactions. Returns memory_ids."""
        extracted = await self._call_preference_extraction(source_text, workspace_id=workspace_id)
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
                                confidence=pref_data.get("confidence", 0.5),
                                scope=pref_data.get("category"),
                                preference_strength=pref_data.get("strength"),
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

        if memory_ids:
            await self._db.flush()
            logger.info("Extracted %d preferences", len(memory_ids))

        return memory_ids

    async def _call_extraction(
        self, source_text: str, prompt_addendum: str | None = None, workspace_id: str = ""
    ) -> dict:
        """Call Claude to extract memories from text. Records a perception token span."""
        try:
            system_prompt = MEMORY_EXTRACTION_PROMPT
            if prompt_addendum:
                system_prompt = system_prompt + prompt_addendum
            text, usage = await complete_text_with_usage(
                system=system_prompt,
                user=source_text,
                tier="resolved",
                max_tokens=1024,
            )
            await record_token_span(
                agent_name="memory",
                model=usage.model,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cache_creation_input_tokens=usage.cache_creation_input_tokens,
                cache_read_input_tokens=usage.cache_read_input_tokens,
                trigger="perception",
                workspace_id=workspace_id,
            )
            from src.llm_utils import coerce_to_object, parse_llm_json

            return coerce_to_object(parse_llm_json(text), list_key="memories")
        except Exception:
            logger.debug("Memory extraction returned non-JSON", exc_info=True)
            return {"memories": []}

    async def _call_preference_extraction(self, source_text: str, workspace_id: str = "") -> dict:
        """Call Claude to extract preferences from text. Records a perception token span."""
        try:
            text, usage = await complete_text_with_usage(
                system=PREFERENCE_EXTRACTION_PROMPT,
                user=source_text,
                tier="resolved",
                max_tokens=1024,
            )
            await record_token_span(
                agent_name="memory",
                model=usage.model,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cache_creation_input_tokens=usage.cache_creation_input_tokens,
                cache_read_input_tokens=usage.cache_read_input_tokens,
                trigger="perception",
                workspace_id=workspace_id,
            )
            from src.llm_utils import coerce_to_object, parse_llm_json

            return coerce_to_object(parse_llm_json(text), list_key="preferences")
        except Exception:
            logger.debug("Preference extraction returned non-JSON", exc_info=True)
            return {"preferences": []}
