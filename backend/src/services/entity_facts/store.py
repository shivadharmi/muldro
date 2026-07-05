"""Bi-temporal entity-attribute fact store (spec §4.6 item 3). Supersede-on-change
(close the old row's valid_to + insert a new current row), corroborate-on-same (raise
confidence), insert-on-new. Reuses the memory-contradiction SHAPE (successor pointer +
confidence adjustment + event emit) with DETERMINISTIC structural detection (same
attr_key, changed value) — no LLM in the write path.

Executes on an injected AsyncSession and flush()es only; the caller owns commit
(mirrors WorldModel / MemoryContradictions)."""

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from ulid import ULID

from src.models.entities import EntityFact
from src.services.entity_facts.confidence import compute_confidence, reliability_for

logger = logging.getLogger(__name__)


def _values_equal(a, b) -> bool:
    """Deterministic structural equality for attribute values (JSON-comparable)."""
    return a == b


class EntityFactStore:
    """Bi-temporal store over the entity_facts table."""

    def __init__(self, db):
        self._db = db

    async def record_fact(
        self,
        *,
        entity_id: str,
        workspace_id: str,
        user_id: str,
        attr_key: str,
        attr_value,
        origin: str,
        source_ref: dict | None = None,
        now: datetime | None = None,
    ) -> tuple[str, bool]:
        """Record an attribute observation. Returns (current_fact_id, superseded).

        - No current fact for (entity, attr_key)      -> insert (superseded=False)
        - Current fact with the SAME value            -> corroborate in place (False)
        - Current fact with a DIFFERENT value         -> close old + insert new (True)
        """
        now = now or datetime.now(timezone.utc)
        current = await self.current_fact(entity_id, attr_key, workspace_id)

        if current is not None and _values_equal(current.attr_value, attr_value):
            current.corroboration_count += 1
            current.confidence = compute_confidence(
                origin=origin, corroboration_count=current.corroboration_count, age_days=0.0
            )
            await self._db.flush()
            return current.fact_id, False

        fact_id = f"fact_{ULID()}"
        if current is not None:
            current.valid_to = now
            current.superseded_by = fact_id

        new_fact = EntityFact(
            fact_id=fact_id,
            entity_id=entity_id,
            workspace_id=workspace_id,
            user_id=user_id,
            attr_key=attr_key,
            attr_value=attr_value,
            corroboration_count=1,
            confidence=compute_confidence(origin=origin, corroboration_count=1, age_days=0.0),
            provenance={
                "origin": origin,
                "source_ref": source_ref,
                "observed_at": now.isoformat(),
                "reliability": reliability_for(origin),
            },
            valid_from=now,
        )
        self._db.add(new_fact)
        await self._db.flush()
        if current is not None:
            logger.info(
                "entity_fact superseded: entity=%s key=%s %s -> %s",
                entity_id,
                attr_key,
                current.fact_id,
                fact_id,
            )
        return fact_id, current is not None

    async def current_fact(self, entity_id: str, attr_key: str, workspace_id: str):
        """The single currently-valid fact for (entity, attr_key), or None. Workspace-scoped."""
        result = await self._db.execute(
            select(EntityFact).where(
                EntityFact.entity_id == entity_id,
                EntityFact.attr_key == attr_key,
                EntityFact.workspace_id == workspace_id,
                EntityFact.valid_to.is_(None),
            )
        )
        return result.scalar_one_or_none()

    async def current_facts(self, entity_id: str, workspace_id: str) -> list[EntityFact]:
        """All currently-valid facts for an entity. Workspace-scoped (fail-closed)."""
        result = await self._db.execute(
            select(EntityFact).where(
                EntityFact.entity_id == entity_id,
                EntityFact.workspace_id == workspace_id,
                EntityFact.valid_to.is_(None),
            )
        )
        return list(result.scalars().all())

    async def facts_as_of(
        self, entity_id: str, workspace_id: str, as_of: datetime
    ) -> list[EntityFact]:
        """The facts that were valid at `as_of` (bi-temporal query). Workspace-scoped."""
        result = await self._db.execute(
            select(EntityFact).where(
                EntityFact.entity_id == entity_id,
                EntityFact.workspace_id == workspace_id,
                EntityFact.valid_from <= as_of,
                (EntityFact.valid_to.is_(None)) | (EntityFact.valid_to > as_of),
            )
        )
        return list(result.scalars().all())

    async def provenance_for(
        self, entity_id: str, workspace_id: str, attr_key: str | None = None
    ) -> list[dict]:
        """Provenance records for the entity's current facts (optionally one key)."""
        facts = await self.current_facts(entity_id, workspace_id)
        return [
            {
                "attr_key": f.attr_key,
                "attr_value": f.attr_value,
                "confidence": f.confidence,
                "corroboration_count": f.corroboration_count,
                "valid_from": f.valid_from.isoformat() if f.valid_from else None,
                "provenance": f.provenance,
            }
            for f in facts
            if attr_key is None or f.attr_key == attr_key
        ]

    async def get_fact(self, fact_id: str):
        result = await self._db.execute(select(EntityFact).where(EntityFact.fact_id == fact_id))
        return result.scalar_one_or_none()

    async def corroborate(self, fact_id: str) -> None:
        """Raise a belief: +1 corroboration, recompute the stored base from its origin.
        Used by post-action reconciliation on a CONFIRMED read-back (spec §4.5)."""
        fact = await self.get_fact(fact_id)
        if fact is None:
            return
        origin = (fact.provenance or {}).get("origin", "unknown")
        fact.corroboration_count += 1
        fact.confidence = compute_confidence(
            origin=origin, corroboration_count=fact.corroboration_count, age_days=0.0
        )
        await self._db.flush()

    async def weaken(self, fact_id: str) -> None:
        """Lower a belief's confidence (halve the stored base — the memory-contradiction
        decay constant). Used on a CONTRADICTED read-back (spec §4.5). Fed to
        abstention only, never the gate."""
        fact = await self.get_fact(fact_id)
        if fact is None:
            return
        fact.confidence = max(0.0, fact.confidence * 0.5)
        await self._db.flush()
