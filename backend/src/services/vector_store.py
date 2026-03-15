"""Qdrant-backed vector store for high-volume RAG operations.

Qdrant is the primary vector store for all RAG operations (memory retrieval,
artifact search, semantic search). pgvector is kept for tight DB-coupled
operations (dedup detection, transaction semantics).
"""

import logging

from src.config.settings import Settings

logger = logging.getLogger(__name__)

# Collection names
COLLECTION_MEMORIES = "memories"
COLLECTION_ENTITIES = "entities"
COLLECTION_EVENTS = "events"
COLLECTION_ARTIFACTS = "artifacts"


class VectorStore:
    """Qdrant-backed vector store with user-scoped filtering."""

    def __init__(self, settings: Settings):
        self._settings = settings
        self._client = None

    async def _get_client(self):
        """Lazy-init Qdrant client."""
        if self._client is None:
            if not self._settings.qdrant_url:
                logger.warning("Qdrant not configured, vector store is no-op")
                return None
            from qdrant_client import AsyncQdrantClient

            self._client = AsyncQdrantClient(
                url=self._settings.qdrant_url,
                api_key=self._settings.qdrant_api_key or None,
            )
        return self._client

    async def ensure_collections(self) -> None:
        """Create collections if they don't exist."""
        client = await self._get_client()
        if not client:
            return

        from qdrant_client.models import Distance, VectorParams

        collections = (
            COLLECTION_MEMORIES,
            COLLECTION_ENTITIES,
            COLLECTION_EVENTS,
            COLLECTION_ARTIFACTS,
        )
        for name in collections:
            try:
                await client.get_collection(name)
            except Exception:
                await client.create_collection(
                    collection_name=name,
                    vectors_config=VectorParams(size=1024, distance=Distance.COSINE),
                )
                logger.info("Created Qdrant collection: %s", name)

    async def upsert(
        self,
        collection: str,
        id: str,
        vector: list[float],
        payload: dict,
        user_id: str,
    ) -> None:
        """Upsert a vector with payload and user_id for tenant isolation."""
        client = await self._get_client()
        if not client:
            return

        from qdrant_client.models import PointStruct

        payload["user_id"] = user_id
        await client.upsert(
            collection_name=collection,
            points=[PointStruct(id=id, vector=vector, payload=payload)],
        )

    async def search(
        self,
        collection: str,
        query_vector: list[float],
        user_id: str,
        filters: dict | None = None,
        limit: int = 10,
    ) -> list[dict]:
        """Search for similar vectors, scoped to user."""
        client = await self._get_client()
        if not client:
            return []

        from qdrant_client.models import FieldCondition, Filter, MatchValue

        conditions = [FieldCondition(key="user_id", match=MatchValue(value=user_id))]
        if filters:
            for key, value in filters.items():
                conditions.append(FieldCondition(key=key, match=MatchValue(value=value)))

        results = await client.search(
            collection_name=collection,
            query_vector=query_vector,
            query_filter=Filter(must=conditions),
            limit=limit,
        )

        return [
            {
                "id": str(r.id),
                "score": r.score,
                "payload": r.payload,
            }
            for r in results
        ]

    async def delete(self, collection: str, id: str) -> None:
        """Delete a point by ID."""
        client = await self._get_client()
        if not client:
            return

        from qdrant_client.models import PointIdsList

        await client.delete(
            collection_name=collection,
            points_selector=PointIdsList(points=[id]),
        )

    async def hybrid_search(
        self,
        user_id: str,
        query_vector: list[float],
        collections: list[str] | None = None,
        limit: int = 20,
    ) -> list[dict]:
        """Search across multiple collections and merge results."""
        if not collections:
            collections = [COLLECTION_MEMORIES, COLLECTION_ENTITIES, COLLECTION_EVENTS]

        all_results = []
        for collection in collections:
            results = await self.search(collection, query_vector, user_id, limit=limit)
            for r in results:
                r["collection"] = collection
            all_results.extend(results)

        # Sort by score descending
        all_results.sort(key=lambda x: x.get("score", 0), reverse=True)
        return all_results[:limit]
