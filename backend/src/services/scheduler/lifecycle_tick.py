"""Data-lifecycle housekeeping: eviction, memory expiration, nightly
consolidation, and Qdrant stability-score refresh."""

import logging

logger = logging.getLogger(__name__)


class LifecycleTickMixin:
    """Eviction, memory expiration, consolidation, and stability refresh."""

    async def _tick_eviction(self, factory) -> None:
        """Run eviction pass to hard-delete expired data with cascade cleanup."""
        try:
            async with factory() as db:
                from src.services.eviction_service import EvictionService

                vector_store = None
                graph_engine = None

                if self._settings.qdrant_url:
                    from src.services.vector_store import VectorStore

                    vector_store = VectorStore(self._settings)
                    await vector_store.ensure_collections()
                    await vector_store.ensure_indexes()

                if self._settings.neo4j_url:
                    from src.services.graph_engine import GraphEngine

                    graph_engine = GraphEngine(self._settings)

                svc = EvictionService(
                    settings=self._settings,
                    db=db,
                    vector_store=vector_store,
                    graph_engine=graph_engine,
                )
                await svc.run_full_eviction()
                await db.commit()

                if graph_engine:
                    await graph_engine.close()
        except Exception:
            logger.warning("Eviction tick error", exc_info=True)

    async def _tick_memory_expiration(self, factory, vector_store=None) -> None:
        """Mark expired memories and cascade delete from Qdrant."""
        try:
            from sqlalchemy import func, select
            from sqlalchemy.dialects.postgresql import INTERVAL
            from sqlalchemy.sql.expression import cast as sa_cast
            from sqlalchemy.sql.expression import literal

            from src.models.memory import Memory

            async with factory() as db:
                # Postgres: created_at + (ttl_days || ' days')::interval < now()
                interval_expr = sa_cast(func.concat(Memory.ttl_days, literal(" days")), INTERVAL)
                result = await db.execute(
                    select(Memory)
                    .where(
                        Memory.status == "active",
                        Memory.ttl_days.isnot(None),
                        Memory.created_at + interval_expr < func.now(),
                    )
                    .limit(100)
                )
                expired = list(result.scalars())

                if not expired:
                    return

                for mem in expired:
                    mem.status = "expired"
                    if vector_store:
                        try:
                            await vector_store.delete("memories", mem.memory_id)
                        except Exception:
                            logger.debug(
                                "Qdrant delete failed for %s",
                                mem.memory_id,
                                exc_info=True,
                            )

                await db.commit()
                logger.info("Memory expiration: %d memories expired", len(expired))
        except Exception:
            logger.warning("Memory expiration tick error", exc_info=True)

    async def _tick_consolidation(self, factory) -> None:
        """Nightly memory consolidation — merge highly similar memories."""
        try:
            async with factory() as db:
                from sqlalchemy import distinct, select

                from src.models.memory import Memory
                from src.services.memory_service import MemoryService

                result = await db.execute(
                    select(distinct(Memory.user_id)).where(Memory.status == "active")
                )
                user_ids = [r[0] for r in result.all()]

                total_merged = 0
                for uid in user_ids:
                    ms = MemoryService(settings=self._settings, db=db)
                    merged = await ms.consolidate_memories(uid)
                    total_merged += merged

                await db.commit()
                if total_merged:
                    logger.info("Nightly consolidation: %d memories merged", total_merged)
        except Exception:
            logger.warning("Memory consolidation tick failed", exc_info=True)

    async def _tick_stability_refresh(self, factory, vector_store=None) -> None:
        """Batch-update Qdrant stability_score for stale memories."""
        if not vector_store:
            return
        try:
            from datetime import datetime, timedelta, timezone

            from sqlalchemy import select

            from src.models.memory import Memory

            async with factory() as db:
                cutoff = datetime.now(timezone.utc) - timedelta(days=7)
                result = await db.execute(
                    select(Memory.memory_id, Memory.stability_score)
                    .where(
                        Memory.status == "active",
                        Memory.last_accessed_at < cutoff,
                    )
                    .limit(200)
                )
                updates = result.all()
                if not updates:
                    return

                for memory_id, stability in updates:
                    try:
                        await vector_store.set_payload(
                            "memories",
                            memory_id,
                            {"stability_score": stability or 0.0},
                        )
                    except Exception:
                        pass  # best-effort per record

                logger.info("Stability refresh: %d Qdrant payloads updated", len(updates))
        except Exception:
            logger.warning("Stability refresh tick failed", exc_info=True)
