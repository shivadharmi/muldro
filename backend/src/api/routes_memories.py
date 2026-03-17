"""Memory listing and retrieval routes."""

import logging

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
    created_at: str | None = None

    model_config = {"from_attributes": True}


class MemoryListResponse(BaseModel):
    memories: list[MemoryItem]


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
                created_at=m.created_at.isoformat() if m.created_at else None,
            )
            for m in rows
        ]
    )
