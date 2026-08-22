"""EvictionService — hard-delete expired data with cascade cleanup.

Runs periodically from SchedulerLoop._tick_eviction().
Handles: memories, sessions, approvals, events.
Cascades to: Qdrant vectors, Neo4j entity graph.

Postgres FTS (tsvector columns) is cleaned automatically when rows are deleted
— no separate FTS cascade needed.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from sqlalchemy import delete, func, select

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from src.config.settings import Settings
    from src.services.graph_engine import GraphEngine
    from src.services.vector_store import VectorStore

logger = logging.getLogger(__name__)

# Grace periods beyond TTL / expires_at before hard delete
MEMORY_EXPIRED_GRACE_DAYS = 7
APPROVAL_RETENTION_DAYS = 30
EVENT_RETENTION_DAYS = 90
INTERACTION_LOG_RETENTION_DAYS = 90
LOW_STABILITY_AGE_DAYS = 60
LOW_STABILITY_THRESHOLD = 0.2
LOW_STABILITY_ACCESS_THRESHOLD = 3

# Batch limits per tick to avoid long-running transactions
MEMORY_BATCH = 500
EVENT_BATCH = 1000
INTERACTION_LOG_BATCH = 1000
LOW_STABILITY_BATCH = 100


class EvictionService:
    """Hard-delete expired records with cascade to external stores.

    Retention contract (Step 5 §6, one-owner-per-fact): the ``runtime_events`` table
    is the durable **system-of-record** and is intentionally EXEMPT from eviction —
    hard-deleting it would make reconcile-from-event-log (Step 10) impossible. This
    service only hard-deletes derivable/replaceable data (normalized_events at 90d,
    memories, sessions, surfaces, approvals, interaction_logs). If bounded retention
    of runtime_events is ever required it MUST be an archive (cold storage), never a
    hard-delete.
    """

    def __init__(
        self,
        settings: Settings,
        db: AsyncSession,
        vector_store: VectorStore | None = None,
        graph_engine: GraphEngine | None = None,
    ):
        self._settings = settings
        self._db = db
        self._vector_store = vector_store
        self._graph_engine = graph_engine

    async def run_full_eviction(self) -> dict[str, int]:
        """Execute all eviction passes. Returns {table: deleted_count}."""
        results: dict[str, int] = {}
        results["memories"] = await self._evict_memories()
        results["sessions"] = await self._evict_sessions()
        results["approvals"] = await self._evict_approvals()
        results["events"] = await self._evict_old_events()
        results["interaction_logs"] = await self._evict_interaction_logs()
        results["low_stability"] = await self._evict_low_stability_memories()
        results["expired_context_packs"] = await self._evict_expired_context_packs()

        total = sum(results.values())
        if total > 0:
            logger.info("Eviction complete: %s (total=%d)", results, total)
        return results

    # ------------------------------------------------------------------
    # Memory eviction (TTL-based + status-based)
    # ------------------------------------------------------------------

    async def _evict_memories(self) -> int:
        """Hard-delete expired memories + cascade to Qdrant."""
        from src.models.memory import Memory

        now = datetime.now(timezone.utc)
        grace = now - timedelta(days=MEMORY_EXPIRED_GRACE_DAYS)

        # Find memories to delete:
        # 1. Already marked "expired" AND past grace period
        # 2. Still "active" but TTL exceeded AND past grace period
        stmt = (
            select(Memory.memory_id)
            .where(
                ((Memory.status == "expired") & (Memory.updated_at < grace))
                | (
                    (Memory.status == "active")
                    & Memory.ttl_days.isnot(None)
                    & (Memory.created_at + func.make_interval(0, 0, 0, Memory.ttl_days) < grace)
                )
            )
            .limit(MEMORY_BATCH)
        )

        result = await self._db.execute(stmt)
        memory_ids = [row[0] for row in result.all()]

        if not memory_ids:
            return 0

        # Cascade: delete from Qdrant
        await self._cascade_qdrant_delete("memories", memory_ids)

        # Hard delete from Postgres (auto-cleans FTS tsvector)
        await self._db.execute(delete(Memory).where(Memory.memory_id.in_(memory_ids)))
        await self._db.flush()

        logger.info("Evicted %d expired memories", len(memory_ids))
        return len(memory_ids)

    # ------------------------------------------------------------------
    # Low-stability memory eviction (proactive cleanup)
    # ------------------------------------------------------------------

    async def _evict_low_stability_memories(self) -> int:
        """Evict old, low-stability, rarely-accessed memories.

        Never evicts goals or preferences — those are user-defined and
        must only be removed explicitly.
        """
        from src.models.memory import Memory

        cutoff = datetime.now(timezone.utc) - timedelta(days=LOW_STABILITY_AGE_DAYS)

        stmt = (
            select(Memory.memory_id)
            .where(
                Memory.status == "active",
                Memory.stability_score < LOW_STABILITY_THRESHOLD,
                Memory.access_count < LOW_STABILITY_ACCESS_THRESHOLD,
                Memory.created_at < cutoff,
                Memory.memory_type.notin_(["goal", "preference"]),
            )
            .order_by(
                Memory.stability_score.asc(),
                Memory.access_count.asc(),
            )
            .limit(LOW_STABILITY_BATCH)
        )

        result = await self._db.execute(stmt)
        memory_ids = [row[0] for row in result.all()]

        if not memory_ids:
            return 0

        await self._cascade_qdrant_delete("memories", memory_ids)

        await self._db.execute(delete(Memory).where(Memory.memory_id.in_(memory_ids)))
        await self._db.flush()

        logger.info("Evicted %d low-stability memories", len(memory_ids))
        return len(memory_ids)

    # ------------------------------------------------------------------
    # Session eviction
    # ------------------------------------------------------------------

    async def _evict_sessions(self) -> int:
        """Hard-delete expired sessions."""
        from src.models.users import Session

        now = datetime.now(timezone.utc)
        result = await self._db.execute(delete(Session).where(Session.expires_at < now))
        count = result.rowcount or 0
        if count:
            await self._db.flush()
        return count

    # ------------------------------------------------------------------
    # Approval eviction
    # ------------------------------------------------------------------

    async def _evict_approvals(self) -> int:
        """Hard-delete decided/expired approvals older than retention period."""
        from src.models.approvals import Approval

        cutoff = datetime.now(timezone.utc) - timedelta(days=APPROVAL_RETENTION_DAYS)
        stmt = select(Approval.approval_id).where(
            Approval.status.in_(["expired", "approved", "rejected"]),
            Approval.created_at < cutoff,
        )
        result = await self._db.execute(stmt)
        approval_ids = [row[0] for row in result.all()]

        if not approval_ids:
            return 0

        await self._cascade_qdrant_delete("approvals", approval_ids)

        await self._db.execute(delete(Approval).where(Approval.approval_id.in_(approval_ids)))
        await self._db.flush()
        logger.info("Evicted %d approvals", len(approval_ids))
        return len(approval_ids)

    # ------------------------------------------------------------------
    # Event eviction (old events past retention)
    # ------------------------------------------------------------------

    async def _evict_old_events(self) -> int:
        """Delete normalized_events older than retention period.

        Cascade: delete from Qdrant vector store.
        Postgres DELETE auto-cleans FTS tsvector columns.
        """
        from src.models.events import NormalizedEvent

        cutoff = datetime.now(timezone.utc) - timedelta(days=EVENT_RETENTION_DAYS)

        # First fetch IDs for cascade cleanup
        stmt = (
            select(NormalizedEvent.event_id)
            .where(NormalizedEvent.occurred_at < cutoff)
            .limit(EVENT_BATCH)
        )
        result = await self._db.execute(stmt)
        event_ids = [row[0] for row in result.all()]

        if not event_ids:
            return 0

        # Cascade: delete from Qdrant
        await self._cascade_qdrant_delete("events", event_ids)

        # Hard delete from Postgres
        await self._db.execute(
            delete(NormalizedEvent).where(NormalizedEvent.event_id.in_(event_ids))
        )
        await self._db.flush()

        logger.info("Evicted %d events older than %d days", len(event_ids), EVENT_RETENTION_DAYS)
        return len(event_ids)

    # ------------------------------------------------------------------
    # Interaction log eviction
    # ------------------------------------------------------------------

    async def _evict_interaction_logs(self) -> int:
        """Delete interaction logs older than retention period."""
        from src.models.interaction_log import InteractionLog

        cutoff = datetime.now(timezone.utc) - timedelta(days=INTERACTION_LOG_RETENTION_DAYS)
        result = await self._db.execute(
            delete(InteractionLog).where(InteractionLog.created_at < cutoff)
        )
        count = result.rowcount or 0
        if count:
            await self._db.flush()
            logger.info("Evicted %d interaction logs", count)
        return count

    # ------------------------------------------------------------------
    # Context-pack TTL eviction (column-level, not a row delete)
    # ------------------------------------------------------------------

    async def _evict_expired_context_packs(self) -> int:
        """NULL context_packs past their TTL (Step 5, D-C3), preserving policy_decision
        on the same row. Not a row delete — the detail row and its durable
        policy_decision survive; only the heavy ephemeral pack is released."""
        from sqlalchemy import update

        from src.models.task_graph import TaskRunDetail

        result = await self._db.execute(
            update(TaskRunDetail)
            .where(
                TaskRunDetail.context_pack.isnot(None),
                TaskRunDetail.context_pack_expires_at.isnot(None),
                TaskRunDetail.context_pack_expires_at < datetime.now(timezone.utc),
            )
            .values(context_pack=None)
        )
        await self._db.flush()
        count = result.rowcount or 0
        if count:
            logger.info("Evicted %d expired context packs", count)
        return count

    # ------------------------------------------------------------------
    # Cascade helpers
    # ------------------------------------------------------------------

    async def _cascade_qdrant_delete(self, collection: str, ids: list[str]) -> None:
        """Delete points from Qdrant by ID. Logs but does not raise on failure."""
        if not self._vector_store:
            return
        for item_id in ids:
            try:
                await self._vector_store.delete(collection, item_id)
            except Exception:
                logger.debug("Qdrant delete failed for %s/%s", collection, item_id, exc_info=True)

    async def cascade_neo4j_delete_entity(self, entity_id: str) -> None:
        """Delete an entity node (+ relationships) from Neo4j."""
        if not self._graph_engine:
            return
        try:
            await self._graph_engine.delete_entity(entity_id)
        except Exception:
            logger.debug("Neo4j delete failed for %s", entity_id, exc_info=True)
