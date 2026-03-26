"""Audit Service — records every significant action with full correlation IDs.

Every external action, policy decision, and state change is logged here.
The audit trail is immutable — entries are never updated or deleted.
"""

import logging

from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID

from src.config.settings import Settings, get_settings
from src.models.audit import AuditLog

logger = logging.getLogger(__name__)


class AuditService:
    """Record audit trail entries."""

    def __init__(self, db: AsyncSession, settings: Settings | None = None):
        self._db = db
        self._settings = settings or get_settings()

    async def log(
        self,
        user_id: str,
        action_type: str,
        *,
        workspace_id: str = "",
        event_id: str | None = None,
        plan_id: str | None = None,
        execution_id: str | None = None,
        approval_id: str | None = None,
        summary: str | None = None,
        artifact_refs: dict | None = None,
        policy_decision: str | None = None,
        details: dict | None = None,
    ) -> str:
        """Create an audit log entry. Returns audit_id."""
        audit_id = f"aud_{ULID()}"
        entry = AuditLog(
            audit_id=audit_id,
            user_id=user_id,
            workspace_id=workspace_id,
            event_id=event_id,
            plan_id=plan_id,
            execution_id=execution_id,
            approval_id=approval_id,
            action_type=action_type,
            summary=summary,
            artifact_refs=artifact_refs,
            policy_decision=policy_decision,
            details=details,
        )
        self._db.add(entry)
        await self._db.flush()
        logger.info("Audit: %s %s %s", audit_id, action_type, summary or "")
        return audit_id
