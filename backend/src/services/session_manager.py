"""Session manager — tracks user sessions across surfaces."""

import logging
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.users import Session

logger = logging.getLogger(__name__)


class SessionManager:
    """Manages user sessions across surfaces (web, mobile)."""

    def __init__(self, db: AsyncSession):
        self._db = db

    async def get_active_sessions(self, user_id: str) -> list[dict]:
        """Get all active (non-revoked, non-expired) sessions for a user."""
        now = datetime.now(timezone.utc)
        result = await self._db.execute(
            select(Session).where(
                Session.user_id == user_id,
                Session.revoked_at.is_(None),
                Session.expires_at > now,
            )
        )
        sessions = result.scalars().all()
        return [
            {
                "session_id": s.session_id,
                "surface": s.surface,
                "device_info": s.device_info,
                "created_at": s.created_at.isoformat() if s.created_at else None,
            }
            for s in sessions
        ]

    async def update_heartbeat(self, session_id: str) -> None:
        """Update last activity timestamp for a session."""
        await self._db.execute(
            update(Session)
            .where(Session.session_id == session_id)
            .values(updated_at=datetime.now(timezone.utc))
        )
        await self._db.flush()

    async def end_session(self, session_id: str) -> None:
        """Revoke a session."""
        result = await self._db.execute(select(Session).where(Session.session_id == session_id))
        session = result.scalar_one_or_none()
        if session:
            session.revoked_at = datetime.now(timezone.utc)
            await self._db.flush()

    async def end_all_sessions(self, user_id: str) -> int:
        """Revoke all sessions for a user."""
        now = datetime.now(timezone.utc)
        result = await self._db.execute(
            update(Session)
            .where(
                Session.user_id == user_id,
                Session.revoked_at.is_(None),
            )
            .values(revoked_at=now)
        )
        await self._db.flush()
        return result.rowcount

    async def get_session_context(self, session_id: str) -> dict | None:
        """Get context for a session (surface, device info)."""
        result = await self._db.execute(select(Session).where(Session.session_id == session_id))
        session = result.scalar_one_or_none()
        if not session:
            return None

        return {
            "session_id": session.session_id,
            "user_id": session.user_id,
            "surface": session.surface,
            "device_info": session.device_info,
        }

    async def cleanup_expired(self) -> int:
        """Revoke expired sessions. Call periodically."""
        now = datetime.now(timezone.utc)
        result = await self._db.execute(
            update(Session)
            .where(
                Session.expires_at < now,
                Session.revoked_at.is_(None),
            )
            .values(revoked_at=now)
        )
        await self._db.flush()
        count = result.rowcount
        if count > 0:
            logger.info("Cleaned up %d expired sessions", count)
        return count
