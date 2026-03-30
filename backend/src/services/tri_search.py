"""TriSearch — federated search across Qdrant, Postgres FTS, and Neo4j.

Embeds the query, fans out to three backends in parallel, merges
results, re-ranks via Bedrock, and computes a composite final score.
Designed as the single entry point for ``ContextBuilder`` and any
API route that needs ranked, multi-source search.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from typing import TYPE_CHECKING

from src.services.fts_service import FTSService

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from src.config.settings import Settings
    from src.services.embedding_service import EmbeddingService
    from src.services.graph_engine import GraphEngine
    from src.services.reranker_service import RerankerService
    from src.services.vector_store import VectorStore

logger = logging.getLogger(__name__)

# ── Composite score weights ──────────────────────────────────────
_W_RERANK = 0.40
_W_RECENCY = 0.25
_W_CONFIDENCE = 0.15
_W_STABILITY = 0.10
_W_ENTITY_OVERLAP = 0.10

# Recency denominator: 30 days in seconds
_RECENCY_WINDOW_SECS = 30 * 86400


def _compute_recency(result: dict) -> float:
    """Return a 0-1 recency score (1 = just created, 0 = 30+ days)."""
    ts = result.get("timestamp") or result.get("created_at")
    if ts is None:
        return 0.5  # neutral default
    if isinstance(ts, (int, float)):
        age = time.time() - ts
    else:
        # ISO string or datetime — best-effort parse
        try:
            from datetime import datetime, timezone

            if isinstance(ts, str):
                dt = datetime.fromisoformat(ts)
            else:
                dt = ts
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            age = (datetime.now(tz=timezone.utc) - dt).total_seconds()
        except Exception:
            return 0.5
    return max(0.0, 1.0 - age / _RECENCY_WINDOW_SECS)


def _compute_final_score(result: dict) -> float:
    """Weighted composite of rerank, recency, confidence, etc."""
    rerank = result.get("rerank_score", result.get("score", 0.0))
    recency = _compute_recency(result)
    confidence = result.get("confidence", 0.5)
    stability = result.get("stability", 0.5)
    entity_overlap = result.get("entity_overlap", 0.0)
    return (
        _W_RERANK * rerank
        + _W_RECENCY * recency
        + _W_CONFIDENCE * confidence
        + _W_STABILITY * stability
        + _W_ENTITY_OVERLAP * entity_overlap
    )


class TriSearchService:
    """Orchestrates search across Qdrant, Postgres FTS, and Neo4j."""

    def __init__(
        self,
        settings: Settings,
        vector_store: VectorStore | None = None,
        graph_engine: GraphEngine | None = None,
        reranker: RerankerService | None = None,
        embedder: EmbeddingService | None = None,
    ) -> None:
        self._settings = settings
        self._vector_store = vector_store
        self._graph_engine = graph_engine
        self._reranker = reranker
        self._embedder = embedder

    # ── Public API ───────────────────────────────────────────────

    async def search(
        self,
        query: str,
        user_id: str,
        workspace_id: str,
        db: AsyncSession,
        types: list[str] | None = None,
        limit: int = 20,
    ) -> list[dict]:
        """Fan-out search, rerank, and score.

        Args:
            query: Natural-language search string.
            user_id: Owner for Qdrant / Neo4j tenant scoping.
            workspace_id: Workspace for Postgres FTS scoping.
            db: Async DB session (used by FTS backend).
            types: Optional filter on ``result_type`` values.
            limit: Maximum results to return.

        Returns:
            List of result dicts sorted by ``final_score`` desc.
        """
        # 0. Clamp limit to safe range
        limit = max(1, min(limit, 100))

        # 1. Embed the query
        embedding = await self._embed_query(query)

        # 2. Fan out to backends in parallel
        qdrant_coro = self._search_qdrant(embedding, user_id, limit)
        fts_coro = self._search_fts(query, workspace_id, db, limit)
        neo4j_coro = self._search_neo4j(query, user_id, limit)

        qdrant_results, fts_results, neo4j_results = await asyncio.gather(
            qdrant_coro,
            fts_coro,
            neo4j_coro,
            return_exceptions=True,
        )

        # Gracefully handle per-backend failures
        merged: list[dict] = []
        failed_count = 0
        for label, results in (
            ("qdrant", qdrant_results),
            ("fts", fts_results),
            ("neo4j", neo4j_results),
        ):
            if isinstance(results, BaseException):
                failed_count += 1
                logger.warning(
                    "TriSearch backend %s failed: %s",
                    label,
                    results,
                )
                continue
            merged.extend(results)

        if failed_count == 3:
            logger.error("All TriSearch backends failed")

        # 3. Optional type filter
        if types:
            type_set = set(types)
            merged = [r for r in merged if r.get("result_type") in type_set]

        # 4. Rerank
        merged = await self._rerank(query, merged, limit)

        # 5. Compute final composite score
        for r in merged:
            r["final_score"] = _compute_final_score(r)

        # 6. Sort and truncate
        merged.sort(key=lambda r: r.get("final_score", 0.0), reverse=True)
        return merged[:limit]

    async def search_for_context(
        self,
        query: str,
        user_id: str,
        workspace_id: str,
        db: AsyncSession,
        limit: int = 20,
    ) -> dict:
        """Search and group results by ``result_type``.

        This is the primary entry point for ``ContextBuilder``.

        Returns:
            ``{"entities": [...], "memories": [...], ...}``
        """
        results = await self.search(
            query=query,
            user_id=user_id,
            workspace_id=workspace_id,
            db=db,
            limit=limit,
        )

        grouped: dict[str, list[dict]] = defaultdict(list)
        for r in results:
            grouped[r.get("result_type", "unknown")].append(r)
        return dict(grouped)

    # ── Backend helpers ──────────────────────────────────────────

    async def _embed_query(self, query: str) -> list[float] | None:
        """Embed query text; returns None when embedder absent."""
        if not self._embedder:
            return None
        try:
            return await self._embedder.embed_text(query)
        except Exception:
            logger.warning(
                "Query embedding failed, skipping vector search",
                exc_info=True,
            )
            return None

    async def _search_qdrant(
        self,
        embedding: list[float] | None,
        user_id: str,
        limit: int,
    ) -> list[dict]:
        """Vector similarity search across Qdrant collections."""
        if not self._vector_store or embedding is None:
            return []

        raw = await self._vector_store.hybrid_search(
            user_id=user_id,
            query_vector=embedding,
            collections=["memories", "events", "artifacts"],
            limit=limit,
        )

        results: list[dict] = []
        for hit in raw:
            payload = hit.get("payload") or {}
            collection = hit.get("collection", "")
            result_type = _collection_to_type(collection)
            results.append(
                {
                    "id": hit.get("id", ""),
                    "title": (
                        payload.get("title")
                        or payload.get("fact_text", "")[:80]
                        or payload.get("canonical_name", "")
                    ),
                    "text": (
                        payload.get("text")
                        or payload.get("fact_text", "")
                        or payload.get("summary", "")
                    ),
                    "score": hit.get("score", 0.0),
                    "source_db": "qdrant",
                    "result_type": result_type,
                    "confidence": payload.get("confidence", 0.5),
                    "stability": payload.get("stability_score", 0.5),
                    "timestamp": payload.get(
                        "created_at",
                        payload.get("occurred_at"),
                    ),
                }
            )
        return results

    async def _search_fts(
        self,
        query: str,
        workspace_id: str,
        db: AsyncSession,
        limit: int,
    ) -> list[dict]:
        """Postgres full-text search via FTSService."""
        fts = FTSService(db, workspace_id)
        return await fts.search(query, limit=limit)

    async def _search_neo4j(
        self,
        query: str,
        user_id: str,
        limit: int,
    ) -> list[dict]:
        """Graph entity search via Neo4j."""
        if not self._graph_engine:
            return []

        # GraphEngine.search_entities may not exist yet — guard
        search_fn = getattr(self._graph_engine, "search_entities", None)
        if search_fn is None:
            return []

        try:
            raw = await search_fn(user_id=user_id, query=query, limit=limit)
        except Exception:
            logger.warning("Neo4j search_entities failed", exc_info=True)
            return []

        results: list[dict] = []
        for item in raw or []:
            results.append(
                {
                    "id": item.get("entity_id", ""),
                    "title": item.get("name", ""),
                    "text": (f"{item.get('name', '')} {item.get('entity_type', '')}").strip(),
                    "score": item.get("score", 0.0),
                    "source_db": "neo4j",
                    "result_type": "entity",
                }
            )
        return results

    async def _rerank(
        self,
        query: str,
        results: list[dict],
        top_k: int,
    ) -> list[dict]:
        """Rerank merged results; pass-through if no reranker."""
        if not self._reranker or not results:
            return results

        try:
            return await self._reranker.rerank(query, results, top_k=top_k)
        except Exception:
            logger.warning(
                "Reranker failed, using raw scores",
                exc_info=True,
            )
            return results


# ── Utilities ────────────────────────────────────────────────────


def _collection_to_type(collection: str) -> str:
    """Map Qdrant collection name to a unified result_type."""
    mapping = {
        "memories": "memory",
        "entities": "entity",
        "events": "event",
        "artifacts": "artifact",
    }
    return mapping.get(collection, collection)
