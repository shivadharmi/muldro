"""Memory Service — episodic, semantic, preference, and behavioral memory.

This is NOT OpenClaw's session memory. This is Jarvis's product memory —
long-term, structured, searchable, and scored.

Responsibilities:
- Extract candidate memories from interactions and events
- Score memory usefulness and stability
- Store with provenance and embedding
- Provide retrieval API scoped by type, entity, and time
- Expire or demote low-value memories
"""


class MemoryService:
    """Manage Jarvis long-term memory."""

    async def extract_and_store(
        self, user_id: str, source_text: str, source_event_ids: list[str]
    ) -> list[str]:
        """Extract memories from text and store them. Returns memory_ids."""
        # TODO: Implement
        # 1. Call Claude to extract candidate memories
        # 2. Score usefulness
        # 3. Dedupe against existing memories
        # 4. Generate embeddings
        # 5. Store in memories table + pgvector
        return []

    async def retrieve(
        self,
        user_id: str,
        query: str,
        memory_types: list[str] | None = None,
        entity_refs: list[str] | None = None,
        max_results: int = 10,
    ) -> list[dict]:
        """Retrieve relevant memories for a given context."""
        # TODO: Implement semantic search with pgvector
        return []
