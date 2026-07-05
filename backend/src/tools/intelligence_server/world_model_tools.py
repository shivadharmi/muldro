"""World-model read tools (spec §4.6 item 5): get_entity / query_facts(as_of) /
traverse / get_provenance. Each is workspace-filtered fail-closed (mirrors the Step-2
hydration gate). Read-only (read_only=True) → verification-exempt. No writes, no gate."""

import logging
from datetime import datetime, timezone

from fastmcp import Context
from fastmcp.server.providers.local_provider.decorators.tools import ToolAnnotations
from sqlalchemy import select

from src.models.entities import Entity, EntityRelationship
from src.services.entity_facts.confidence import current_confidence
from src.services.entity_facts.store import EntityFactStore
from src.tools.intelligence_server._shared import _get_db, intelligence

logger = logging.getLogger(__name__)


def _days_since(ts) -> float:
    if ts is None:
        return 0.0
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return max(0.0, (datetime.now(timezone.utc) - ts).total_seconds() / 86400.0)


def _fact_dict(f) -> dict:
    return {
        "attr_key": f.attr_key,
        "attr_value": f.attr_value,
        "confidence": current_confidence(f.confidence, age_days=_days_since(f.valid_from)),
        "corroboration_count": f.corroboration_count,
        "valid_from": f.valid_from.isoformat() if f.valid_from else None,
        "valid_to": f.valid_to.isoformat() if f.valid_to else None,
        "provenance": f.provenance,
    }


@intelligence.tool(
    tags={"librarian", "read"},
    annotations=ToolAnnotations(readOnlyHint=True),
)
async def get_entity(
    user_id: str,
    entity_id: str,
    ctx: Context,
    workspace_id: str = "",
) -> dict:
    """Fetch a world-model entity and its current attribute beliefs (with confidence
    and provenance). Workspace-scoped."""
    async with _get_db() as db:
        ent = (
            await db.execute(
                select(Entity).where(
                    Entity.entity_id == entity_id,
                    Entity.user_id == user_id,
                    Entity.workspace_id == workspace_id,
                )
            )
        ).scalar_one_or_none()
        if ent is None:
            return {"entity": None, "facts": []}
        facts = await EntityFactStore(db).current_facts(entity_id, workspace_id)
        return {
            "entity": {
                "entity_id": ent.entity_id,
                "entity_type": ent.entity_type,
                "canonical_name": ent.canonical_name,
                "confidence": current_confidence(
                    ent.confidence_score, age_days=_days_since(ent.last_seen_at)
                ),
                "attributes": ent.attributes,
            },
            "facts": [_fact_dict(f) for f in facts],
        }


@intelligence.tool(
    tags={"librarian", "read"},
    annotations=ToolAnnotations(readOnlyHint=True),
)
async def query_facts(
    user_id: str,
    entity_id: str,
    ctx: Context,
    as_of: str = "",
    workspace_id: str = "",
) -> dict:
    """Query an entity's attribute beliefs as-of a timestamp (ISO-8601; empty = now).
    Returns the beliefs valid at that time (bi-temporal). Workspace-scoped."""
    async with _get_db() as db:
        ent = (
            await db.execute(
                select(Entity.entity_id).where(
                    Entity.entity_id == entity_id,
                    Entity.workspace_id == workspace_id,
                )
            )
        ).scalar_one_or_none()
        if ent is None:
            return {"facts": [], "as_of": as_of}
        when = _parse_iso(as_of) or datetime.now(timezone.utc)
        facts = await EntityFactStore(db).facts_as_of(entity_id, workspace_id, when)
        return {"facts": [_fact_dict(f) for f in facts], "as_of": when.isoformat()}


@intelligence.tool(
    tags={"librarian", "read"},
    annotations=ToolAnnotations(readOnlyHint=True),
)
async def traverse(
    user_id: str,
    entity_id: str,
    ctx: Context,
    workspace_id: str = "",
) -> dict:
    """List the relationships incident to an entity (one hop). Workspace-scoped
    fail-closed (explicit workspace_id filter)."""
    async with _get_db() as db:
        result = await db.execute(
            select(EntityRelationship).where(
                EntityRelationship.workspace_id == workspace_id,
                (EntityRelationship.from_entity_id == entity_id)
                | (EntityRelationship.to_entity_id == entity_id),
            )
        )
        rels = result.scalars().all()
        return {
            "relationships": [
                {
                    "relation_id": r.relation_id,
                    "from_entity_id": r.from_entity_id,
                    "relation_type": r.relation_type,
                    "to_entity_id": r.to_entity_id,
                    "active": r.active,
                }
                for r in rels
            ]
        }


@intelligence.tool(
    tags={"librarian", "read"},
    annotations=ToolAnnotations(readOnlyHint=True),
)
async def get_provenance(
    user_id: str,
    entity_id: str,
    ctx: Context,
    attr_key: str = "",
    workspace_id: str = "",
) -> dict:
    """Provenance for an entity's current beliefs — origin, source_ref, observed_at,
    reliability, confidence. Optionally one attr_key. Workspace-scoped."""
    async with _get_db() as db:
        ent = (
            await db.execute(
                select(Entity.entity_id).where(
                    Entity.entity_id == entity_id,
                    Entity.workspace_id == workspace_id,
                )
            )
        ).scalar_one_or_none()
        if ent is None:
            return {"provenance": []}
        records = await EntityFactStore(db).provenance_for(
            entity_id, workspace_id, attr_key or None
        )
        return {"provenance": records}


def _parse_iso(value: str) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None
