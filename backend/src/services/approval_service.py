"""Centralized approval factory — single path to create Approval records.

Ensures requested_by and workspace_id are ALWAYS populated.
Used by Governor, hooks.py, and GraphExecutor.
"""

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID

from src.models.approvals import Approval

logger = logging.getLogger(__name__)

DEFAULT_EXPIRY_HOURS = 24


async def create_approval(
    db: AsyncSession,
    *,
    user_id: str,
    workspace_id: str,
    approval_type: str,
    title: str,
    summary: str | None = None,
    risk_level: str = "medium",
    execution_id: str = "",
    run_id: str | None = None,
    step_id: str | None = None,
    requested_by: str,
    artifact_refs: dict | None = None,
    expires_at: datetime | None = None,
) -> Approval:
    """Create an Approval record with all required fields populated.

    This is the ONLY way to create approvals. Direct Approval() construction
    is prohibited in Governor, hooks, and GraphExecutor.
    """
    approval_id = f"apr_{ULID()}"

    if expires_at is None:
        expires_at = datetime.now(timezone.utc) + timedelta(hours=DEFAULT_EXPIRY_HOURS)

    approval = Approval(
        approval_id=approval_id,
        user_id=user_id,
        workspace_id=workspace_id,
        execution_id=execution_id,
        approval_type=approval_type,
        title=title,
        summary=summary,
        artifact_refs=artifact_refs,
        risk_level=risk_level,
        status="pending",
        expires_at=expires_at,
        run_id=run_id,
        step_id=step_id,
        requested_by=requested_by,
    )
    db.add(approval)

    logger.info(
        "approval_created",
        extra={
            "approval_id": approval_id,
            "user_id": user_id,
            "workspace_id": workspace_id,
            "approval_type": approval_type,
            "requested_by": requested_by,
            "risk_level": risk_level,
        },
    )

    return approval
