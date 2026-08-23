"""Centralized approval factory — single path to create Approval records.

Ensures requested_by and workspace_id are ALWAYS populated.
Used by Governor, hooks.py, and GraphExecutor.
"""

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID

from src.models.approvals import PREPARED_APPROVAL_TYPE, Approval

logger = logging.getLogger(__name__)

DEFAULT_EXPIRY_HOURS = 24

# Approval types that never expire. Staged work is a fully-derived external write the
# founder has NOT yet seen, and a timer is not a reviewer: dropping it means the action
# is gone with nobody having decided anything. Run-linked approvals still expire — there
# a deadline is real, because a run is parked on the answer.
#
# The rule lives here rather than at each gate because ``expires_at=None`` already means
# "use the default" below. A caller trying to express "never" by passing None would get
# 24 HOURS — four times shorter than the TTL it replaced — so the intent cannot safely be
# left to callers to spell.
NON_EXPIRING_TYPES = frozenset({PREPARED_APPROVAL_TYPE})


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
    # Validate artifact_refs for tool-level approvals
    if artifact_refs and approval_type and approval_type.startswith("tool:"):
        if "tool_name" not in artifact_refs:
            raise ValueError(
                f"Tool-level approval requires 'tool_name' in artifact_refs, "
                f"got keys: {list(artifact_refs.keys())}"
            )

    approval_id = f"apr_{ULID()}"

    if expires_at is None and approval_type not in NON_EXPIRING_TYPES:
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
        thread_id=(artifact_refs or {}).get("thread_id"),
        tool_call_id=(artifact_refs or {}).get("tool_call_id"),
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
