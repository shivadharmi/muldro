"""InteractionLearner — extract durable memories from user interactions.

Runs asynchronously after each non-trivial interaction so that Jarvis
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
    ) -> None:
        self._settings = settings
        self._db_factory = db_factory
        self._vector_store = vector_store
        self._redis = redis

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
        cooldown_key = f"jarvis:learn_cooldown:{user_id}"
        try:
            acquired = await self._redis.set(cooldown_key, "1", ex=_COOLDOWN_SECONDS, nx=True)
            if not acquired:
                logger.debug("Learning cooldown active for %s, skipping", user_id)
                return
        except Exception:
            # Redis down — proceed without cooldown protection
            logger.debug("Redis cooldown check failed, proceeding", exc_info=True)

        # Build combined source text
        source_text = f"User: {user_message}\nJarvis: {agent_response}"

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
        except Exception:
            logger.warning("Interaction learning failed (trace=%s)", trace_id, exc_info=True)
