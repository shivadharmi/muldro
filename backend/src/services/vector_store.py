"""Qdrant-backed vector store for high-volume RAG operations.

Qdrant is the sole vector store for all operations: memory retrieval,
artifact search, semantic search, dedup detection, and contradiction checks.
"""

import logging
import uuid

from src.config.settings import Settings

logger = logging.getLogger(__name__)

# Namespace UUID for converting string IDs to UUID5 (Qdrant requires UUID or int IDs)
_QDRANT_NS = uuid.UUID("a3f1b2c4-d5e6-4f78-9a0b-1c2d3e4f5a6b")


def _to_qdrant_id(string_id: str) -> str:
    """Convert a string ID (e.g. evt_01KM...) to a UUID5 string for Qdrant."""
    return str(uuid.uuid5(_QDRANT_NS, string_id))


# Collection names
COLLECTION_MEMORIES = "memories"
COLLECTION_ENTITIES = "entities"
COLLECTION_EVENTS = "events"
COLLECTION_ARTIFACTS = "artifacts"
COLLECTION_CONVERSATIONS = "conversations"
COLLECTION_APPROVALS = "approvals"

# Vector dimensions (Bedrock Titan V2)
VECTOR_SIZE = 1024


class VectorStore:
    """Qdrant-backed vector store with user-scoped filtering."""

    def __init__(self, settings: Settings):
        self._settings = settings
        self._client = None

    async def _get_client(self):
        """Lazy-init Qdrant client with reconnection on failure."""
        if self._client is not None:
            try:
                await self._client.get_collections()
                return self._client
            except Exception:
                logger.warning("Qdrant health check failed, reconnecting")
                self._client = None

        if not self._settings.qdrant_url:
            logger.warning("Qdrant not configured, vector store is no-op")
            return None
        from qdrant_client import AsyncQdrantClient

        self._client = AsyncQdrantClient(
            url=self._settings.qdrant_url,
            api_key=self._settings.qdrant_api_key or None,
        )
        return self._client

    async def close(self) -> None:
        """Close the Qdrant client connection."""
        if self._client:
            await self._client.close()
            self._client = None

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
            COLLECTION_CONVERSATIONS,
            COLLECTION_APPROVALS,
        )
        from qdrant_client.http.exceptions import UnexpectedResponse

        for name in collections:
            try:
                await client.get_collection(name)
            except UnexpectedResponse:
                await client.create_collection(
                    collection_name=name,
                    vectors_config=VectorParams(size=VECTOR_SIZE, distance=Distance.COSINE),
                )
                logger.info("Created Qdrant collection: %s", name)
            except Exception:
                logger.warning("Qdrant ensure_collections failed for %s", name, exc_info=True)

    async def ensure_indexes(self) -> None:
        """Create Qdrant payload indexes for filtered search."""
        client = await self._get_client()
        if not client:
            return
        from qdrant_client.http.exceptions import UnexpectedResponse
        from qdrant_client.models import PayloadSchemaType

        indexes = {
            COLLECTION_MEMORIES: [
                ("memory_type", PayloadSchemaType.KEYWORD),
                ("confidence", PayloadSchemaType.FLOAT),
            ],
            COLLECTION_ENTITIES: [
                ("entity_type", PayloadSchemaType.KEYWORD),
            ],
            COLLECTION_EVENTS: [
                ("source", PayloadSchemaType.KEYWORD),
                ("event_type", PayloadSchemaType.KEYWORD),
                ("importance_score", PayloadSchemaType.FLOAT),
            ],
        }
        for collection, fields in indexes.items():
            for field_name, schema_type in fields:
                try:
                    await client.create_payload_index(
                        collection_name=collection,
                        field_name=field_name,
                        field_schema=schema_type,
                    )
                except UnexpectedResponse:
                    pass  # index already exists
                except Exception:
                    logger.warning(
                        "Qdrant create_payload_index failed: %s.%s",
                        collection,
                        field_name,
                        exc_info=True,
                    )

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
        payload["_original_id"] = id
        qdrant_id = _to_qdrant_id(id)
        await client.upsert(
            collection_name=collection,
            points=[PointStruct(id=qdrant_id, vector=vector, payload=payload)],
        )

    async def batch_upsert(
        self,
        collection: str,
        points: list[dict],
        user_id: str,
    ) -> int:
        """Batch upsert multiple vectors.

        Each point dict: {"id": str, "vector": list[float], "payload": dict}
        Returns count of upserted points.
        """
        client = await self._get_client()
        if not client:
            return 0

        from qdrant_client.models import PointStruct

        qdrant_points = []
        for p in points:
            payload = dict(p.get("payload", {}))
            payload["user_id"] = user_id
            payload["_original_id"] = p["id"]
            qdrant_points.append(
                PointStruct(
                    id=_to_qdrant_id(p["id"]),
                    vector=p["vector"],
                    payload=payload,
                )
            )

        if qdrant_points:
            await client.upsert(collection_name=collection, points=qdrant_points)
        return len(qdrant_points)

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

        response = await client.query_points(
            collection_name=collection,
            query=query_vector,
            query_filter=Filter(must=conditions),
            limit=limit,
            with_payload=True,
        )

        return [
            {
                "id": (r.payload or {}).get("_original_id", str(r.id)),
                "score": r.score,
                "payload": r.payload,
            }
            for r in response.points
        ]

    async def find_similar(
        self,
        collection: str,
        query_vector: list[float],
        user_id: str,
        threshold: float = 0.9,
        limit: int = 5,
        filters: dict | None = None,
    ) -> list[dict]:
        """Find items above a similarity threshold. For dedup/contradiction checks."""
        results = await self.search(collection, query_vector, user_id, filters=filters, limit=limit)
        return [r for r in results if r.get("score", 0) >= threshold]

    async def delete(self, collection: str, id: str) -> None:
        """Delete a point by ID."""
        client = await self._get_client()
        if not client:
            return

        qdrant_id = _to_qdrant_id(id)
        await client.delete(
            collection_name=collection,
            points_selector=[qdrant_id],
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
            collections = [
                COLLECTION_MEMORIES,
                COLLECTION_ENTITIES,
                COLLECTION_EVENTS,
                COLLECTION_CONVERSATIONS,
                COLLECTION_APPROVALS,
            ]

        all_results = []
        for collection in collections:
            results = await self.search(collection, query_vector, user_id, limit=limit)
            for r in results:
                r["collection"] = collection
            all_results.extend(results)

        # Sort by score descending
        all_results.sort(key=lambda x: x.get("score", 0), reverse=True)
        return all_results[:limit]
