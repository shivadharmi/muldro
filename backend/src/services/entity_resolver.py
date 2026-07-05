"""Span-based entity resolution — the world-model read path (replaces
ILIKE-on-raw-message, spec §4.6 item 1).

Per candidate span, gather entity_id candidates from three signals:
  1. exact  — canonical_name == span OR alias == span (workspace-scoped, strongest)
  2. FTS    — the activated entities.search_vector via FTSService
  3. vector — Qdrant `entities` collection similarity (best-effort; optional deps)

then HYDRATE the merged candidates through a workspace-scoped DB query, which is
the authoritative isolation gate — cross-workspace ids from the (best-effort)
vector signal are dropped here, fail-closed. Returns the same dict shape as
WorldModel.find_entity so downstream ranking is unchanged.
"""

from __future__ import annotations

import logging

from sqlalchemy import or_, select

from src.models.entities import Entity, EntityAlias
from src.services.entity_facts.confidence import current_confidence, days_since
from src.services.entity_spans import extract_spans
from src.services.fts_service import FTSService

logger = logging.getLogger(__name__)

_EXACT_SCORE = 1.0
# FTS ts_rank is small/unbounded; band it strictly BELOW exact (1.0) and above
# noise so the ordering "exact > FTS > vector-only" holds regardless of rank size.
_FTS_SCORE_FLOOR = 0.5
_FTS_SCORE_CEILING = 0.9


def _exact_match_stmt(user_id: str, span: str, workspace_id: str):
    """Exact canonical-name or exact-alias match, workspace-scoped. Extracted so
    isolation tests can compile it (mirrors world_model._find_entity_stmt)."""
    return select(Entity.entity_id).where(
        Entity.user_id == user_id,
        Entity.workspace_id == workspace_id,
        or_(
            Entity.canonical_name == span,
            Entity.entity_id.in_(
                select(EntityAlias.entity_id).where(
                    EntityAlias.alias == span,
                    EntityAlias.workspace_id == workspace_id,
                )
            ),
        ),
    )


def _hydrate_entities_stmt(user_id: str, entity_ids: list[str], workspace_id: str):
    """Workspace-scoped hydration of candidate ids — the authoritative isolation
    gate for resolution results."""
    return select(Entity).where(
        Entity.user_id == user_id,
        Entity.workspace_id == workspace_id,
        Entity.entity_id.in_(entity_ids),
    )


def _to_dict(e: Entity) -> dict:
    """Same shape WorldModel.find_entity returns (drop-in for _rank_entities)."""
    return {
        "entity_id": e.entity_id,
        "entity_type": e.entity_type,
        "canonical_name": e.canonical_name,
        "attributes": e.attributes,
        "importance_score": e.importance_score,
        "interaction_count": e.interaction_count,
        "last_seen_at": (e.last_seen_at.isoformat() if e.last_seen_at else None),
        "confidence": current_confidence(e.confidence_score, age_days=days_since(e.last_seen_at)),
        "provenance": {"origin_hint": e.entity_type},
    }


class EntityResolver:
    def __init__(self, db, workspace_id: str, embedding_service=None, vector_store=None):
        self._db = db
        self._workspace_id = workspace_id
        self._embedding_service = embedding_service
        self._vector_store = vector_store
        self._fts = FTSService(db, workspace_id)

    async def resolve(self, user_id: str, text: str, limit: int = 10) -> list[dict]:
        spans = extract_spans(text)
        if not spans:
            return []

        scores: dict[str, float] = {}
        for span in spans:
            for entity_id, score in await self._span_candidates(user_id, span):
                if score > scores.get(entity_id, 0.0):
                    scores[entity_id] = score
        if not scores:
            return []

        # Keep a headroom of 2x before hydration so the workspace filter can drop
        # cross-workspace vector ids without starving the final list.
        ranked_ids = [eid for eid, _ in sorted(scores.items(), key=lambda kv: kv[1], reverse=True)][
            : max(limit * 2, limit)
        ]

        result = await self._db.execute(
            _hydrate_entities_stmt(user_id, ranked_ids, self._workspace_id)
        )
        by_id = {e.entity_id: e for e in result.scalars().all()}
        ordered = [by_id[eid] for eid in ranked_ids if eid in by_id][:limit]
        return [_to_dict(e) for e in ordered]

    async def _span_candidates(self, user_id: str, span: str) -> list[tuple[str, float]]:
        out: list[tuple[str, float]] = []

        # 1. Exact (strongest).
        exact = await self._db.execute(_exact_match_stmt(user_id, span, self._workspace_id))
        out.extend((eid, _EXACT_SCORE) for eid in exact.scalars().all())

        # 2. FTS over the activated search_vector.
        try:
            fts = await self._fts.search_table("entities", span, limit=5)
            # ts_rank values are small/unbounded — band FTS below exact, above noise.
            out.extend(
                (r["id"], min(_FTS_SCORE_CEILING, _FTS_SCORE_FLOOR + float(r.get("score", 0.0))))
                for r in fts
            )
        except Exception:
            logger.debug("entity FTS failed for span=%r", span, exc_info=True)

        # 3. Vector (best-effort; optional deps).
        if self._embedding_service and self._vector_store:
            try:
                vec = await self._embedding_service.embed_text(span)
                if vec:
                    sim = await self._vector_store.search(
                        "entities",
                        vec,
                        user_id,
                        filters={"workspace_id": self._workspace_id}
                        if self._workspace_id
                        else None,
                        limit=5,
                    )
                    out.extend((r["id"], float(r.get("score", 0.0))) for r in sim)
            except Exception:
                logger.debug("entity vector search failed for span=%r", span, exc_info=True)

        return out
