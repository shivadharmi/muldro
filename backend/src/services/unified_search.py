"""Unified Search Service — federated search across all data types.

Searches conversations, briefings, approvals, traces, goals, entities,
memories, and artifacts. Returns grouped results with why_matched and actions.
"""

from __future__ import annotations

import logging

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


class UnifiedSearchResult:
    """A single search result with metadata."""

    __slots__ = (
        "result_type",
        "result_id",
        "title",
        "snippet",
        "score",
        "why_matched",
        "actions",
        "metadata",
    )

    def __init__(
        self,
        result_type: str,
        result_id: str,
        title: str,
        snippet: str = "",
        score: float = 0.0,
        why_matched: str = "",
        actions: list[dict] | None = None,
        metadata: dict | None = None,
    ):
        self.result_type = result_type
        self.result_id = result_id
        self.title = title
        self.snippet = snippet
        self.score = score
        self.why_matched = why_matched
        self.actions = actions or []
        self.metadata = metadata or {}

    def to_dict(self) -> dict:
        return {
            "result_type": self.result_type,
            "result_id": self.result_id,
            "title": self.title,
            "snippet": self.snippet,
            "score": self.score,
            "why_matched": self.why_matched,
            "actions": self.actions,
            "metadata": self.metadata,
        }


class UnifiedSearchService:
    """Federated search across all Jarvis data types.

    Uses Elasticsearch for full-text search when available (fast, scalable),
    falls back to Postgres ILIKE queries when ES is not configured.
    """

    def __init__(
        self,
        db: AsyncSession,
        workspace_id: str,
        search_service=None,
    ):
        self._db = db
        self._workspace_id = workspace_id
        self._search_service = search_service

    async def search(
        self,
        query: str,
        *,
        types: list[str] | None = None,
        limit: int = 20,
    ) -> dict:
        """Search across all types, returning grouped results.

        Args:
            query: Search query string.
            types: Optional filter — only search these types.
            limit: Max results per type.

        Returns:
            Dict with groups, total_count, and query echo.
        """
        all_types = types or [
            "conversation",
            "briefing",
            "approval",
            "entity",
            "memory",
            "goal",
        ]

        groups: dict[str, list[dict]] = {}
        total = 0

        searchers = {
            "conversation": self._search_conversations,
            "briefing": self._search_briefings,
            "approval": self._search_approvals,
            "entity": self._search_entities,
            "memory": self._search_memories,
            "goal": self._search_goals,
        }

        for result_type in all_types:
            searcher = searchers.get(result_type)
            if not searcher:
                continue
            results = await searcher(query, limit)
            if results:
                groups[result_type] = [r.to_dict() for r in results]
                total += len(results)

        return {
            "query": query,
            "total_count": total,
            "groups": groups,
        }

    async def _es_query(
        self,
        index: str,
        query: str,
        fields: list[str],
        limit: int,
        extra_filters: list[dict] | None = None,
    ) -> list[dict] | None:
        """Run an ES multi_match query. Returns None if ES unavailable."""
        if not self._search_service:
            return None
        try:
            es = await self._search_service._get_es()
            if not es:
                return None
            filters = [{"term": {"workspace_id": self._workspace_id}}]
            if extra_filters:
                filters.extend(extra_filters)
            resp = await es.search(
                index=index,
                body={
                    "query": {
                        "bool": {
                            "must": [{"multi_match": {"query": query, "fields": fields}}],
                            "filter": filters,
                        }
                    },
                    "size": limit,
                },
            )
            return [
                {"id": hit["_id"], "source": hit["_source"], "score": hit["_score"]}
                for hit in resp["hits"]["hits"]
            ]
        except Exception:
            logger.debug("ES query failed for %s, falling back to Postgres", index)
            return None

    async def _search_conversations(self, query: str, limit: int) -> list[UnifiedSearchResult]:
        es_hits = await self._es_query("jarvis-conversations", query, ["title"], limit)
        if es_hits is not None:
            return [
                UnifiedSearchResult(
                    result_type="conversation",
                    result_id=h["id"],
                    title=h["source"].get("title", "Untitled"),
                    snippet="",
                    score=h["score"],
                    why_matched="title match",
                    actions=[{"action": "open", "url": f"/chat?c={h['id']}"}],
                )
                for h in es_hits
            ]

        from src.models.conversations import Conversation

        q = f"%{query}%"
        result = await self._db.execute(
            select(Conversation)
            .where(
                Conversation.workspace_id == self._workspace_id,
                Conversation.title.ilike(q),
            )
            .order_by(Conversation.updated_at.desc())
            .limit(limit)
        )
        return [
            UnifiedSearchResult(
                result_type="conversation",
                result_id=c.conversation_id,
                title=c.title or "Untitled",
                snippet="",
                why_matched="title match",
                actions=[{"action": "open", "url": f"/chat?c={c.conversation_id}"}],
            )
            for c in result.scalars().all()
        ]

    async def _search_briefings(self, query: str, limit: int) -> list[UnifiedSearchResult]:
        es_hits = await self._es_query("jarvis-briefings", query, ["headline", "full_text"], limit)
        if es_hits is not None:
            return [
                UnifiedSearchResult(
                    result_type="briefing",
                    result_id=h["id"],
                    title=h["source"].get("headline", "Briefing"),
                    snippet=(h["source"].get("full_text", ""))[:150],
                    score=h["score"],
                    why_matched="content match",
                    actions=[{"action": "open", "url": f"/briefings/{h['id']}"}],
                )
                for h in es_hits
            ]

        from src.models.briefings import Briefing

        q = f"%{query}%"
        result = await self._db.execute(
            select(Briefing)
            .where(
                Briefing.workspace_id == self._workspace_id,
                or_(
                    Briefing.headline.ilike(q),
                    Briefing.full_text.ilike(q),
                ),
            )
            .order_by(Briefing.created_at.desc())
            .limit(limit)
        )
        return [
            UnifiedSearchResult(
                result_type="briefing",
                result_id=b.briefing_id,
                title=b.headline or f"Briefing {b.date}",
                snippet=(b.full_text or "")[:150],
                why_matched="content match",
                actions=[{"action": "open", "url": f"/briefings/{b.briefing_id}"}],
            )
            for b in result.scalars().all()
        ]

    async def _search_approvals(self, query: str, limit: int) -> list[UnifiedSearchResult]:
        es_hits = await self._es_query("jarvis-approvals", query, ["title", "summary"], limit)
        if es_hits is not None:
            return [
                UnifiedSearchResult(
                    result_type="approval",
                    result_id=h["id"],
                    title=h["source"].get("title", "Approval"),
                    snippet=(h["source"].get("summary", ""))[:150],
                    score=h["score"],
                    why_matched="title/summary match",
                    actions=[{"action": "open", "url": f"/approvals/{h['id']}"}],
                    metadata={
                        "status": h["source"].get("status", ""),
                        "risk_level": h["source"].get("risk_level", ""),
                    },
                )
                for h in es_hits
            ]

        from src.models.approvals import Approval

        q = f"%{query}%"
        result = await self._db.execute(
            select(Approval)
            .where(
                Approval.workspace_id == self._workspace_id,
                or_(
                    Approval.title.ilike(q),
                    Approval.summary.ilike(q),
                ),
            )
            .order_by(Approval.created_at.desc())
            .limit(limit)
        )
        return [
            UnifiedSearchResult(
                result_type="approval",
                result_id=a.approval_id,
                title=a.title or "Approval",
                snippet=(a.summary or "")[:150],
                why_matched="title/summary match",
                actions=[{"action": "open", "url": f"/approvals/{a.approval_id}"}],
                metadata={"status": a.status, "risk_level": a.risk_level},
            )
            for a in result.scalars().all()
        ]

    async def _search_entities(self, query: str, limit: int) -> list[UnifiedSearchResult]:
        es_hits = await self._es_query("jarvis-entities", query, ["canonical_name"], limit)
        if es_hits is not None:
            return [
                UnifiedSearchResult(
                    result_type="entity",
                    result_id=h["id"],
                    title=h["source"].get("canonical_name", ""),
                    snippet=h["source"].get("entity_type", ""),
                    score=h["score"],
                    why_matched="name match",
                    metadata={"entity_type": h["source"].get("entity_type", "")},
                )
                for h in es_hits
            ]

        from src.models.entities import Entity

        q = f"%{query}%"
        result = await self._db.execute(
            select(Entity)
            .where(
                Entity.workspace_id == self._workspace_id,
                Entity.canonical_name.ilike(q),
            )
            .order_by(Entity.updated_at.desc())
            .limit(limit)
        )
        return [
            UnifiedSearchResult(
                result_type="entity",
                result_id=e.entity_id,
                title=e.canonical_name or "",
                snippet=e.entity_type or "",
                why_matched="name match",
                metadata={"entity_type": e.entity_type},
            )
            for e in result.scalars().all()
        ]

    async def _search_memories(self, query: str, limit: int) -> list[UnifiedSearchResult]:
        es_hits = await self._es_query("jarvis-memories", query, ["fact_text"], limit)
        if es_hits is not None:
            return [
                UnifiedSearchResult(
                    result_type="memory",
                    result_id=h["id"],
                    title=(h["source"].get("fact_text", ""))[:80],
                    snippet=(h["source"].get("fact_text", ""))[:200],
                    score=h["score"],
                    why_matched="content match",
                    metadata={"memory_type": h["source"].get("memory_type", "")},
                )
                for h in es_hits
            ]

        from src.models.memory import Memory

        q = f"%{query}%"
        result = await self._db.execute(
            select(Memory)
            .where(
                Memory.workspace_id == self._workspace_id,
                Memory.fact_text.ilike(q),
            )
            .order_by(Memory.updated_at.desc())
            .limit(limit)
        )
        return [
            UnifiedSearchResult(
                result_type="memory",
                result_id=m.memory_id,
                title=(m.fact_text or "")[:80],
                snippet=(m.fact_text or "")[:200],
                why_matched="content match",
                metadata={"memory_type": m.memory_type},
            )
            for m in result.scalars().all()
        ]

    async def _search_goals(self, query: str, limit: int) -> list[UnifiedSearchResult]:
        es_hits = await self._es_query(
            "jarvis-memories",
            query,
            ["fact_text"],
            limit,
            extra_filters=[
                {"term": {"memory_type": "goal"}},
            ],
        )
        if es_hits is not None:
            return [
                UnifiedSearchResult(
                    result_type="goal",
                    result_id=h["id"],
                    title=h["source"].get("fact_text", ""),
                    snippet="",
                    score=h["score"],
                    why_matched="title match",
                    metadata={"memory_type": "goal"},
                )
                for h in es_hits
            ]

        from src.models.memory import Memory

        q = f"%{query}%"
        result = await self._db.execute(
            select(Memory)
            .where(
                Memory.workspace_id == self._workspace_id,
                Memory.memory_type == "goal",
                Memory.status == "active",
                Memory.fact_text.ilike(q),
            )
            .order_by(Memory.created_at.desc())
            .limit(limit)
        )
        return [
            UnifiedSearchResult(
                result_type="goal",
                result_id=g.memory_id,
                title=g.fact_text or "",
                snippet="",
                score=g.confidence or 0.5,
                why_matched="title match",
                metadata={"memory_type": "goal"},
            )
            for g in result.scalars().all()
        ]
