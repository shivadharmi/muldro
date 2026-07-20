"""Dead-letter queue service — capture and retry failed operations.

When an operation fails after retries, it lands here for manual
inspection or automated retry during heartbeat cycles.
"""

import logging
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID

from src.models.dead_letter import DeadLetterEntry

logger = logging.getLogger(__name__)


class DeadLetterService:
    """Manage the dead-letter queue for failed operations."""

    def __init__(self, db: AsyncSession):
        self._db = db

    async def enqueue(
        self,
        user_id: str,
        operation_type: str,
        error_type: str,
        error_message: str,
        source_id: str | None = None,
        payload: dict | None = None,
        max_attempts: int = 3,
        workspace_id: str = "",
    ) -> str:
        """Add a failed operation to the dead-letter queue. Returns entry_id."""
        entry_id = f"dlq_{ULID()}"

        entry = DeadLetterEntry(
            entry_id=entry_id,
            user_id=user_id,
            workspace_id=workspace_id,
            operation_type=operation_type,
            source_id=source_id,
            error_type=type(Exception).__name__ if not error_type else error_type,
            error_message=error_message[:2000] if error_message else None,
            payload=payload,
            max_attempts=max_attempts,
            status="pending",
        )

        self._db.add(entry)
        await self._db.flush()

        logger.warning(
            "Dead-letter enqueued: %s op=%s source=%s error=%s",
            entry_id,
            operation_type,
            source_id,
            error_message[:100] if error_message else "unknown",
        )
        return entry_id

    async def list_pending(
        self,
        user_id: str,
        operation_type: str | None = None,
        limit: int = 50,
        workspace_id: str = "",
    ) -> list[DeadLetterEntry]:
        """List pending dead-letter entries for a user."""
        query = select(DeadLetterEntry).where(
            DeadLetterEntry.user_id == user_id,
            DeadLetterEntry.status.in_(["pending", "retrying"]),
        )
        if workspace_id:
            query = query.where(DeadLetterEntry.workspace_id == workspace_id)
        if operation_type:
            query = query.where(DeadLetterEntry.operation_type == operation_type)
        query = query.order_by(DeadLetterEntry.created_at.desc()).limit(limit)

        result = await self._db.execute(query)
        return list(result.scalars().all())

    async def mark_retrying(self, entry_id: str) -> bool:
        """Mark an entry as being retried. Returns False if exhausted."""
        result = await self._db.execute(
            select(DeadLetterEntry).where(DeadLetterEntry.entry_id == entry_id)
        )
        entry = result.scalar_one_or_none()
        if not entry:
            return False

        if entry.attempt_count >= entry.max_attempts:
            entry.status = "exhausted"
            await self._db.flush()
            logger.info("Dead-letter exhausted: %s after %d attempts", entry_id, entry.max_attempts)
            return False

        entry.status = "retrying"
        entry.attempt_count += 1
        # DateTime column needs a datetime, not an ISO string (asyncpg is strict).
        entry.last_attempted_at = datetime.now(timezone.utc)
        await self._db.flush()
        return True

    async def mark_resolved(self, entry_id: str) -> None:
        """Mark a dead-letter entry as resolved."""
        await self._db.execute(
            update(DeadLetterEntry)
            .where(DeadLetterEntry.entry_id == entry_id)
            .values(
                status="resolved",
                resolved_at=datetime.now(timezone.utc),
            )
        )
        await self._db.flush()
        logger.info("Dead-letter resolved: %s", entry_id)

    async def get_stats(self, user_id: str, workspace_id: str = "") -> dict:
        """Get dead-letter queue statistics."""
        conditions = [DeadLetterEntry.user_id == user_id]
        if workspace_id:
            conditions.append(DeadLetterEntry.workspace_id == workspace_id)
        result = await self._db.execute(
            select(DeadLetterEntry.status, DeadLetterEntry.operation_type).where(*conditions)
        )
        rows = result.all()

        stats: dict = {"total": len(rows), "by_status": {}, "by_operation": {}}
        for status, op_type in rows:
            stats["by_status"][status] = stats["by_status"].get(status, 0) + 1
            stats["by_operation"][op_type] = stats["by_operation"].get(op_type, 0) + 1
        return stats
