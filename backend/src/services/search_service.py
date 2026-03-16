"""Hybrid search service — Elasticsearch + Qdrant + Postgres.

Provides unified search across events, entities, memories, and artifacts
using reciprocal rank fusion to merge results from multiple backends.
"""

import logging

from src.config.settings import Settings
from src.services.embedding_service import EmbeddingService
from src.services.vector_store import VectorStore

logger = logging.getLogger(__name__)


class SearchService:
    """Hybrid search across Elasticsearch (BM25) + Qdrant (semantic) + Postgres."""

    def __init__(self, settings: Settings, vector_store: VectorStore | None = None):
        self._settings = settings
        self._vector_store = vector_store
        self._embedder = EmbeddingService(settings)
        self._es = None

    async def _get_es(self):
        """Lazy-init Elasticsearch client."""
        if self._es is None and self._settings.elasticsearch_url:
            from elasticsearch import AsyncElasticsearch

            self._es = AsyncElasticsearch(
                hosts=[self._settings.elasticsearch_url],
                verify_certs=False,
            )
        return self._es

    async def ensure_indices(self) -> None:
        """Create ES indices if they don't exist."""
        es = await self._get_es()
        if not es:
            return

        indices = {
            "jarvis-events": {
                "properties": {
                    "user_id": {"type": "keyword"},
                    "event_type": {"type": "keyword"},
                    "source": {"type": "keyword"},
                    "title": {"type": "text"},
                    "summary": {"type": "text"},
                    "occurred_at": {"type": "date"},
                }
            },
            "jarvis-entities": {
                "properties": {
                    "user_id": {"type": "keyword"},
                    "entity_type": {"type": "keyword"},
                    "canonical_name": {"type": "text"},
                    "attributes": {"type": "object", "enabled": False},
                }
            },
            "jarvis-memories": {
                "properties": {
                    "user_id": {"type": "keyword"},
                    "memory_type": {"type": "keyword"},
                    "fact_text": {"type": "text"},
                    "confidence": {"type": "float"},
                }
            },
        }

        for index_name, mapping in indices.items():
            if not await es.indices.exists(index=index_name):
                await es.indices.create(
                    index=index_name,
                    body={"mappings": mapping},
                )
                logger.info("Created ES index: %s", index_name)

    async def index_event(self, event_id: str, user_id: str, data: dict) -> None:
        """Index an event to ES and Qdrant."""
        es = await self._get_es()
        if es:
            await es.index(
                index="jarvis-events",
                id=event_id,
                body={"user_id": user_id, **data},
            )

        if self._vector_store:
            text = f"{data.get('title', '')} {data.get('summary', '')}"
            embedding = await self._embedder.embed_text(text)
            if embedding:
                await self._vector_store.upsert(
                    "events", event_id, embedding, {"user_id": user_id, **data}, user_id
                )

    async def index_entity(self, entity_id: str, user_id: str, data: dict) -> None:
        """Index an entity to ES and Qdrant."""
        es = await self._get_es()
        if es:
            await es.index(
                index="jarvis-entities",
                id=entity_id,
                body={"user_id": user_id, **data},
            )

        if self._vector_store:
            text = f"{data.get('canonical_name', '')} {data.get('entity_type', '')}"
            embedding = await self._embedder.embed_text(text)
            if embedding:
                await self._vector_store.upsert(
                    "entities", entity_id, embedding, {"user_id": user_id, **data}, user_id
                )

    async def index_memory(self, memory_id: str, user_id: str, data: dict) -> None:
        """Index a memory to ES and Qdrant."""
        es = await self._get_es()
        if es:
            await es.index(
                index="jarvis-memories",
                id=memory_id,
                body={"user_id": user_id, **data},
            )

    async def index_artifact(self, artifact_id: str, user_id: str, data: dict) -> None:
        """Index an artifact to ES and Qdrant."""
        es = await self._get_es()
        if es:
            if not await es.indices.exists(index="jarvis-artifacts"):
                await es.indices.create(
                    index="jarvis-artifacts",
                    body={
                        "mappings": {
                            "properties": {
                                "user_id": {"type": "keyword"},
                                "artifact_type": {"type": "keyword"},
                                "title": {"type": "text"},
                                "mime_type": {"type": "keyword"},
                            }
                        }
                    },
                )
            await es.index(
                index="jarvis-artifacts",
                id=artifact_id,
                body={"user_id": user_id, **data},
            )

        if self._vector_store:
            text = data.get("title", "")
            embedding = await self._embedder.embed_text(text)
            if embedding:
                await self._vector_store.upsert(
                    "artifacts",
                    artifact_id,
                    embedding,
                    {"user_id": user_id, **data},
                    user_id,
                )

    async def reindex_all(
        self,
        user_id: str,
        events: list[dict] | None = None,
        entities: list[dict] | None = None,
        memories: list[dict] | None = None,
        artifacts: list[dict] | None = None,
    ) -> dict:
        """Bulk reindex items to ES and Qdrant."""
        counts = {"events": 0, "entities": 0, "memories": 0, "artifacts": 0}

        for event in events or []:
            await self.index_event(event["event_id"], user_id, event)
            counts["events"] += 1

        for entity in entities or []:
            await self.index_entity(entity["entity_id"], user_id, entity)
            counts["entities"] += 1

        for memory in memories or []:
            await self.index_memory(memory["memory_id"], user_id, memory)
            counts["memories"] += 1

        for artifact in artifacts or []:
            await self.index_artifact(artifact["artifact_id"], user_id, artifact)
            counts["artifacts"] += 1

        logger.info("Reindexed for user %s: %s", user_id, counts)
        return counts

    async def search(
        self,
        user_id: str,
        query: str,
        scopes: list[str] | None = None,
        limit: int = 20,
    ) -> list[dict]:
        """Hybrid search: ES (BM25) + Qdrant (semantic), merged via RRF."""
        if not scopes:
            scopes = ["events", "entities", "memories"]

        # Semantic search via Qdrant
        semantic_results = []
        if self._vector_store:
            embedding = await self._embedder.embed_text(query)
            if embedding:
                collections = [f for f in scopes if f in ("memories", "entities", "events")]
                semantic_results = await self._vector_store.hybrid_search(
                    user_id, embedding, collections, limit=limit
                )

        # Full-text search via ES
        es_results = []
        es = await self._get_es()
        if es:
            index_map = {
                "events": "jarvis-events",
                "entities": "jarvis-entities",
                "memories": "jarvis-memories",
            }
            indices = [index_map[s] for s in scopes if s in index_map]
            if indices:
                resp = await es.search(
                    index=",".join(indices),
                    body={
                        "query": {
                            "bool": {
                                "must": [
                                    {
                                        "multi_match": {
                                            "query": query,
                                            "fields": [
                                                "title",
                                                "summary",
                                                "fact_text",
                                                "canonical_name",
                                            ],
                                        },
                                    }
                                ],
                                "filter": [{"term": {"user_id": user_id}}],
                            }
                        },
                        "size": limit,
                    },
                )
                for hit in resp["hits"]["hits"]:
                    es_results.append(
                        {
                            "id": hit["_id"],
                            "score": hit["_score"],
                            "source": hit["_source"],
                            "index": hit["_index"],
                        }
                    )

        # Reciprocal Rank Fusion
        return self._rrf_merge(semantic_results, es_results, limit)

    @staticmethod
    def _rrf_merge(
        semantic: list[dict], fulltext: list[dict], limit: int, k: int = 60
    ) -> list[dict]:
        """Merge results using Reciprocal Rank Fusion."""
        scores: dict[str, float] = {}
        items: dict[str, dict] = {}

        for rank, item in enumerate(semantic):
            item_id = item.get("id", "")
            scores[item_id] = scores.get(item_id, 0) + 1 / (k + rank + 1)
            items[item_id] = item

        for rank, item in enumerate(fulltext):
            item_id = item.get("id", "")
            scores[item_id] = scores.get(item_id, 0) + 1 / (k + rank + 1)
            items[item_id] = item

        sorted_ids = sorted(scores, key=lambda x: scores[x], reverse=True)
        return [{**items[item_id], "rrf_score": scores[item_id]} for item_id in sorted_ids[:limit]]
