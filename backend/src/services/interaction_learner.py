"""InteractionLearner — extract durable memories from user interactions.

Runs asynchronously after each non-trivial interaction so that Muldro
builds continuity over time without slowing the user-facing response.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from src.services.memory_service import MemoryService

if TYPE_CHECKING:
    from src.config.settings import Settings
    from src.services.vector_store import VectorStore

logger = logging.getLogger(__name__)

# Intents that produce no meaningful knowledge — skip learning.
SKIP_LEARNING_INTENTS = frozenset(
    {
        "greeting",
        "chitchat",
        "acknowledgment",
        "simple_question",
        "memory_operation",
    }
)

# Appended to the extraction prompt for interaction-sourced text.
_INTERACTION_ADDENDUM = """

When the input is a user-agent dialogue:
- Extract factual knowledge the agent discovered (entities, counts, states, dates)
- Extract user behavioral signals (what topics they care about, what they check on)
- Prefer semantic and preference memories over episodic for recurring patterns
- Do NOT extract the act of asking itself as a memory ("User asked about X" is low value)
"""

# Redis cooldown window in seconds — prevents burst extraction.
_COOLDOWN_SECONDS = 60


class InteractionLearner:
    """Extract and store memories from user-agent interactions.

    Creates a fresh MemoryService + DB session per learn() call so that
    background tasks don't share the runtime session (which would cause
    concurrency issues and missing commits).
    """

    def __init__(
        self,
        settings: Settings,
        db_factory,
        vector_store: VectorStore | None = None,
        redis=None,
        event_bus=None,
    ) -> None:
        self._settings = settings
        self._db_factory = db_factory
        self._vector_store = vector_store
        self._redis = redis
        self._event_bus = event_bus

    async def learn(
        self,
        user_id: str,
        workspace_id: str,
        user_message: str,
        agent_response: str,
        intent: str | None,
        trace_id: str,
    ) -> None:
        """Extract memories from a completed interaction (fire-and-forget).

        Skips extraction when:
        - The intent is trivial (greeting, chitchat, etc.)
        - The agent response is empty
        - A cooldown window is active for this user (60s)
        """
        # Gate 1: intent filter
        if intent in SKIP_LEARNING_INTENTS:
            return

        # Gate 2: empty response
        if not agent_response or not agent_response.strip():
            return

        # Gate 3: Redis cooldown — prevent burst extraction
        cooldown_key = f"muldro:learn_cooldown:{user_id}"
        try:
            acquired = await self._redis.set(cooldown_key, "1", ex=_COOLDOWN_SECONDS, nx=True)
            if not acquired:
                logger.debug("Learning cooldown active for %s, skipping", user_id)
                return
        except Exception:
            # Redis down — proceed without cooldown protection
            logger.debug("Redis cooldown check failed, proceeding", exc_info=True)

        # Build combined source text
        source_text = f"User: {user_message}\nMuldro: {agent_response}"

        # Provenance metadata for source tagging
        provenance_extra = {
            "source": "interaction",
            "intent": intent,
            "trace_id": trace_id,
        }

        try:
            # Fresh DB session + MemoryService per call — background tasks must
            # not share the runtime session (no auto-commit, concurrency issues).
            async with self._db_factory() as db:
                mem_svc = MemoryService(
                    settings=self._settings,
                    db=db,
                    vector_store=self._vector_store,
                    event_bus=self._event_bus,
                )
                memory_ids = await mem_svc.extract_and_store(
                    user_id=user_id,
                    source_text=source_text,
                    source_event_ids=[trace_id],
                    workspace_id=workspace_id,
                    prompt_addendum=_INTERACTION_ADDENDUM,
                    provenance_extra=provenance_extra,
                )
                if memory_ids:
                    await db.commit()
                    logger.info(
                        "Interaction learning stored %d memories (trace=%s)",
                        len(memory_ids),
                        trace_id,
                    )

                # Entity extraction → Neo4j sync (best-effort, same pattern
                # as store_memory in intelligence_server.py)
                entity_ids = await self._extract_entities(db, source_text, user_id, workspace_id)
                if entity_ids:
                    await self._sync_to_graph(db, entity_ids)
        except Exception:
            logger.warning("Interaction learning failed (trace=%s)", trace_id, exc_info=True)

    async def _extract_entities(self, db, text: str, user_id: str, workspace_id: str) -> list[str]:
        """Extract entities from text via WorldModel (best-effort)."""
        try:
            from src.services.provenance import SourceRef
            from src.services.world_model import WorldModel

            wm = WorldModel(self._settings, db, vector_store=self._vector_store)
            entity_ids = await wm.extract_from_text(
                text,
                user_id=user_id,
                workspace_id=workspace_id,
                source_ref=SourceRef(source="interaction"),
            )
            if entity_ids:
                await db.commit()
                logger.debug("Interaction learning extracted %d entities", len(entity_ids))
            return entity_ids or []
        except Exception:
            logger.debug("Entity extraction from interaction failed", exc_info=True)
            return []

    async def _sync_to_graph(self, db, entity_ids: list[str]) -> None:
        """Sync extracted entities to Neo4j (best-effort)."""
        if not self._settings.neo4j_url:
            return
        try:
            from src.services.graph_sync import GraphSyncService

            gs = GraphSyncService(self._settings, db)
            await gs.batch_sync_entities(entity_ids)
            await gs.close()
            logger.debug("Interaction learning synced %d entities to Neo4j", len(entity_ids))
        except Exception:
            logger.debug("Neo4j sync from interaction failed", exc_info=True)
