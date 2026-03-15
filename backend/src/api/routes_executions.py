"""Execution listing and detail routes."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_current_user_id, get_session
from src.models.executions import Execution

router = APIRouter()
logger = logging.getLogger(__name__)


class ExecutionItem(BaseModel):
    execution_id: str
    plan_id: str
    status: str
    current_task_id: str | None = None
    errors: dict | None = None
    created_at: str | None = None

    model_config = {"from_attributes": True}


@router.get("/v1/executions", response_model=list[ExecutionItem])
async def list_executions(
    status: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_session),
):
    """List executions for the current user."""
    stmt = select(Execution).where(Execution.user_id == user_id)

    if status:
        stmt = stmt.where(Execution.status == status)

    stmt = stmt.order_by(Execution.created_at.desc()).limit(limit)

    result = await db.execute(stmt)
    rows = result.scalars().all()

    return [
        ExecutionItem(
            execution_id=e.execution_id,
            plan_id=e.plan_id,
            status=e.status,
            current_task_id=e.current_task_id,
            errors=e.errors,
            created_at=e.created_at.isoformat() if e.created_at else None,
        )
        for e in rows
    ]


@router.get("/v1/executions/{execution_id}", response_model=ExecutionItem)
async def get_execution(
    execution_id: str,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_session),
):
    """Get a single execution by ID."""
    result = await db.execute(
        select(Execution).where(
            Execution.execution_id == execution_id, Execution.user_id == user_id
        )
    )
    exc = result.scalar_one_or_none()
    if not exc:
        raise HTTPException(status_code=404, detail="Execution not found")

    return ExecutionItem(
        execution_id=exc.execution_id,
        plan_id=exc.plan_id,
        status=exc.status,
        current_task_id=exc.current_task_id,
        errors=exc.errors,
        created_at=exc.created_at.isoformat() if exc.created_at else None,
    )
