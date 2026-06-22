"""Workspace resolution for background (non-request) code paths.

Canonical home for ``resolve_workspace_id`` so services (scheduler, worker,
notifier) import it downward from the service layer instead
of upward from ``src.api``. ``api.deps`` re-exports it for route handlers.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.users import WorkspaceMember


async def resolve_workspace_id(db: AsyncSession, user_id: str) -> str:
    """Resolve workspace_id from user_id for background services.

    API routes should use ``get_current_workspace_id()`` instead (zero extra
    queries). This is for scheduler, worker, perception, and other non-request
    code paths.
    """
    result = await db.execute(
        select(WorkspaceMember.workspace_id)
        .where(WorkspaceMember.user_id == user_id, WorkspaceMember.role == "owner")
        .limit(1)
    )
    ws_id = result.scalar_one_or_none()
    if not ws_id:
        raise ValueError(f"No workspace found for user {user_id}")
    return ws_id
