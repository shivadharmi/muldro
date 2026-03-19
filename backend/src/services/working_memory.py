"""Working memory — session-scoped ephemeral state for active tasks.

Provides fast key-value storage with TTL for in-progress task context,
discourse state, and intermediate results. Entries auto-expire.
"""

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID

from src.models.working_memory import WorkingMemoryEntry

logger = logging.getLogger(__name__)


class WorkingMemoryService:
    """Session-scoped working memory for active task context."""

    def __init__(self, db: AsyncSession):
        self._db = db

    async def set(
        self,
        user_id: str,
        key: str,
        value: object,
        session_id: str | None = None,
        entry_type: str = "variable",
        ttl_seconds: int = 3600,
        workspace_id: str = "",
    ) -> str:
        """Set a working memory entry (upsert)."""
        result = await self._db.execute(
            select(WorkingMemoryEntry).where(
                WorkingMemoryEntry.user_id == user_id,
                WorkingMemoryEntry.key == key,
                WorkingMemoryEntry.session_id == session_id
                if session_id
                else WorkingMemoryEntry.session_id.is_(None),
            )
        )
        existing = result.scalar_one_or_none()

        expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)

        if existing:
            existing.value = value
            existing.ttl_seconds = ttl_seconds
            existing.expires_at = expires_at
            existing.entry_type = entry_type
            await self._db.flush()
            return existing.entry_id

        entry_id = f"wm_{ULID()}"
        entry = WorkingMemoryEntry(
            entry_id=entry_id,
            user_id=user_id,
            workspace_id=workspace_id,
            session_id=session_id,
            entry_type=entry_type,
            key=key,
            value=value,
            ttl_seconds=ttl_seconds,
            expires_at=expires_at,
        )
        self._db.add(entry)
        await self._db.flush()
        return entry_id

    async def get(
        self, user_id: str, key: str, session_id: str | None = None, workspace_id: str = ""
    ) -> object | None:
        """Get a working memory value. Returns None if expired or missing."""
        conditions = [
            WorkingMemoryEntry.user_id == user_id,
            WorkingMemoryEntry.key == key,
            WorkingMemoryEntry.session_id == session_id
            if session_id
            else WorkingMemoryEntry.session_id.is_(None),
        ]
        if workspace_id:
            conditions.append(WorkingMemoryEntry.workspace_id == workspace_id)
        result = await self._db.execute(select(WorkingMemoryEntry).where(*conditions))
        entry = result.scalar_one_or_none()
        if not entry:
            return None

        if entry.expires_at and entry.expires_at < datetime.now(timezone.utc):
            await self._db.delete(entry)
            await self._db.flush()
            return None

        return entry.value

    async def get_all(
        self, user_id: str, session_id: str | None = None, workspace_id: str = ""
    ) -> dict[str, object]:
        """Get all non-expired entries for a user/session."""
        now = datetime.now(timezone.utc)
        query = select(WorkingMemoryEntry).where(
            WorkingMemoryEntry.user_id == user_id,
        )
        if workspace_id:
            query = query.where(WorkingMemoryEntry.workspace_id == workspace_id)
        if session_id:
            query = query.where(WorkingMemoryEntry.session_id == session_id)

        result = await self._db.execute(query)
        entries = result.scalars().all()

        data = {}
        for entry in entries:
            if entry.expires_at and entry.expires_at < now:
                continue
            data[entry.key] = entry.value
        return data

    async def get_task_focus(self, user_id: str, workspace_id: str = "") -> list[dict]:
        """Get all active task focus entries."""
        now = datetime.now(timezone.utc)
        conditions = [
            WorkingMemoryEntry.user_id == user_id,
            WorkingMemoryEntry.entry_type == "task_focus",
        ]
        if workspace_id:
            conditions.append(WorkingMemoryEntry.workspace_id == workspace_id)
        result = await self._db.execute(select(WorkingMemoryEntry).where(*conditions))
        entries = result.scalars().all()
        return [
            {"key": e.key, "value": e.value}
            for e in entries
            if not e.expires_at or e.expires_at >= now
        ]

    async def set_task_focus(self, user_id: str, task_id: str, context: dict) -> str:
        """Set a task focus entry with long TTL."""
        return await self.set(
            user_id=user_id,
            key=f"task_focus:{task_id}",
            value=context,
            entry_type="task_focus",
            ttl_seconds=86400,  # 24 hours
        )

    async def delete(self, user_id: str, key: str, session_id: str | None = None) -> None:
        """Delete a specific entry."""
        conditions = [
            WorkingMemoryEntry.user_id == user_id,
            WorkingMemoryEntry.key == key,
        ]
        if session_id:
            conditions.append(WorkingMemoryEntry.session_id == session_id)
        else:
            conditions.append(WorkingMemoryEntry.session_id.is_(None))

        await self._db.execute(delete(WorkingMemoryEntry).where(*conditions))
        await self._db.flush()

    async def clear_session(self, session_id: str) -> int:
        """Clear all entries for a session."""
        result = await self._db.execute(
            delete(WorkingMemoryEntry).where(WorkingMemoryEntry.session_id == session_id)
        )
        await self._db.flush()
        return result.rowcount

    async def cleanup_expired(self) -> int:
        """Remove all expired entries. Call periodically."""
        now = datetime.now(timezone.utc)
        result = await self._db.execute(
            delete(WorkingMemoryEntry).where(WorkingMemoryEntry.expires_at < now)
        )
        await self._db.flush()
        count = result.rowcount
        if count > 0:
            logger.info("Cleaned up %d expired working memory entries", count)
        return count
