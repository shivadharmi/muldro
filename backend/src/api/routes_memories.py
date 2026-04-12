"""Memory listing and retrieval routes."""

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_current_user_id, get_current_workspace_id, get_session
from src.models.memory import Memory

router = APIRouter()
logger = logging.getLogger(__name__)


class MemoryItem(BaseModel):
    memory_id: str
    memory_type: str
    scope: str | None = None
    fact_text: str
    confidence: float
    status: str
    last_accessed_at: str | None = None
    is_stale: bool = False
    entity_ids: list[str] = []
    access_count: int = 0
    created_at: str | None = None

    model_config = {"from_attributes": True}


class MemoryListResponse(BaseModel):
    memories: list[MemoryItem]


def _is_stale(last_accessed_at: datetime | None) -> bool:
    if last_accessed_at is None:
        return True
    return last_accessed_at < datetime.now(timezone.utc) - timedelta(days=7)


@router.get("/v1/memories", response_model=MemoryListResponse)
async def list_memories(
    memory_type: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    user_id: str = Depends(get_current_user_id),
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_session),
):
    """List memories for the current user."""
    stmt = select(Memory).where(
        Memory.user_id == user_id, Memory.workspace_id == workspace_id, Memory.status == "active"
    )

    if memory_type:
        stmt = stmt.where(Memory.memory_type == memory_type)

    stmt = stmt.order_by(Memory.created_at.desc()).limit(limit)

    result = await db.execute(stmt)
    rows = result.scalars().all()

    return MemoryListResponse(
        memories=[
            MemoryItem(
                memory_id=m.memory_id,
                memory_type=m.memory_type,
                scope=m.scope,
                fact_text=m.fact_text,
                confidence=m.confidence,
                status=m.status,
                last_accessed_at=(m.last_accessed_at.isoformat() if m.last_accessed_at else None),
                is_stale=_is_stale(m.last_accessed_at),
                entity_ids=m.entity_ids or [],
                access_count=getattr(m, "access_count", 0) or 0,
                created_at=m.created_at.isoformat() if m.created_at else None,
            )
            for m in rows
        ]
    )


@router.get("/v1/memories/{memory_id}/provenance")
async def get_memory_provenance(
    memory_id: str,
    user_id: str = Depends(get_current_user_id),
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_session),
):
    """Get provenance information for a memory."""
    from src.services.memory_influence import MemoryInfluenceService

    svc = MemoryInfluenceService(db, workspace_id)
    provenance = await svc.get_provenance(memory_id)
    if not provenance:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail=f"Memory {memory_id} not found")

    return {
        "memory_id": provenance.memory_id,
        "source_event_id": provenance.source_event_id,
        "created_by_agent": provenance.created_by_agent,
        "created_at": provenance.created_at.isoformat() if provenance.created_at else None,
        "access_count": provenance.access_count,
        "last_accessed_at": (
            provenance.last_accessed_at.isoformat() if provenance.last_accessed_at else None
        ),
        "influenced_plan_ids": provenance.influenced_plan_ids,
        "influenced_briefing_ids": provenance.influenced_briefing_ids,
    }


@router.get("/v1/memories/{memory_id}/influence")
async def get_memory_influence(
    memory_id: str,
    user_id: str = Depends(get_current_user_id),
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_session),
):
    """Get influence references for a memory."""
    from src.services.memory_influence import MemoryInfluenceService

    svc = MemoryInfluenceService(db, workspace_id)
    refs = await svc.get_influence_refs(memory_id)
    return {
        "memory_id": memory_id,
        "references": [
            {
                "ref_type": r.ref_type,
                "ref_id": r.ref_id,
                "used_at": r.used_at.isoformat() if r.used_at else None,
                "context": r.context,
            }
            for r in refs
        ],
    }


@router.get("/v1/memories/conflicts")
async def get_memory_conflicts(
    limit: int = Query(10, ge=1, le=50),
    user_id: str = Depends(get_current_user_id),
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_session),
):
    """Detect potentially conflicting memories."""
    from src.services.memory_influence import MemoryInfluenceService

    svc = MemoryInfluenceService(db, workspace_id)
    conflicts = await svc.detect_conflicts(user_id, limit)
    return {
        "conflicts": [
            {
                "memory_a_id": c.memory_a_id,
                "memory_a_text": c.memory_a_text,
                "memory_b_id": c.memory_b_id,
                "memory_b_text": c.memory_b_text,
                "conflict_type": c.conflict_type,
            }
            for c in conflicts
        ],
    }


@router.get("/v1/memories/review")
async def get_review_queue(
    limit: int = Query(20, ge=1, le=100),
    user_id: str = Depends(get_current_user_id),
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_session),
):
    """Get memories that need human review."""
    from src.services.memory_influence import MemoryInfluenceService

    svc = MemoryInfluenceService(db, workspace_id)
    memories = await svc.get_review_queue(user_id, limit)
    return {
        "review_queue": [
            MemoryItem(
                memory_id=m.memory_id,
                memory_type=m.memory_type,
                scope=m.scope,
                fact_text=m.fact_text,
                confidence=m.confidence,
                status=m.status,
                last_accessed_at=(m.last_accessed_at.isoformat() if m.last_accessed_at else None),
                is_stale=_is_stale(m.last_accessed_at),
                entity_ids=m.entity_ids or [],
                access_count=getattr(m, "access_count", 0) or 0,
                created_at=m.created_at.isoformat() if m.created_at else None,
            ).model_dump()
            for m in memories
        ],
    }


@router.post("/v1/memories/{memory_id}/archive")
async def archive_memory(
    memory_id: str,
    user_id: str = Depends(get_current_user_id),
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_session),
):
    """Archive a memory (soft delete)."""
    from src.services.memory_influence import MemoryInfluenceService

    svc = MemoryInfluenceService(db, workspace_id)
    await svc.archive_memory(memory_id)
    await db.commit()
    return {"status": "archived", "memory_id": memory_id}


@router.get("/v1/memories/stats")
async def get_memory_stats(
    user_id: str = Depends(get_current_user_id),
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_session),
):
    """Get memory statistics for the current user."""
    from src.services.memory_influence import MemoryInfluenceService

    svc = MemoryInfluenceService(db, workspace_id)
    stats = await svc.get_stats(user_id)
    return stats


@router.delete("/v1/memories/{memory_id}")
async def delete_memory(
    memory_id: str,
    user_id: str = Depends(get_current_user_id),
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_session),
):
    """Archive a memory via DELETE (set status to expired). Used to remove instructions/goals."""
    result = await db.execute(
        select(Memory).where(
            Memory.memory_id == memory_id,
            Memory.user_id == user_id,
            Memory.workspace_id == workspace_id,
        )
    )
    memory = result.scalar_one_or_none()
    if not memory:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="Memory not found")

    memory.status = "expired"
    await db.commit()

    # Cascade: remove from Qdrant so the memory is no longer searchable
    try:
        from src.config.settings import get_settings
        from src.services.vector_store import VectorStore

        settings = get_settings()
        if settings.qdrant_url:
            vector_store = VectorStore(settings)
            await vector_store.delete("memories", memory_id)
    except Exception:
        logger.warning("Qdrant delete failed for memory %s", memory_id, exc_info=True)

    return {"status": "archived", "memory_id": memory_id}
