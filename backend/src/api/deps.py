"""FastAPI dependency injection."""

from fastapi import Depends, Header, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.settings import Settings, get_settings
from src.models.database import get_db
from src.models.users import User


async def get_current_user(
    authorization: str | None = Header(None),
    token: str | None = Query(None, description="Auth token (for SSE/EventSource)"),
    settings: Settings = Depends(get_settings),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Validate the request and return the authenticated User.

    Supports two modes:
    1. Session token: Bearer <session_token> (primary auth)
    2. Query param token: ?token=<token> (for SSE/EventSource which can't set headers)
    """
    # If no Authorization header, check query param (for SSE)
    if (not authorization or not authorization.startswith("Bearer ")) and token:
        authorization = f"Bearer {token}"

    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing authorization")

    raw_token = authorization.removeprefix("Bearer ")

    # Session-based auth
    from src.services.auth_service import AuthService

    auth = AuthService(settings, db)
    user = await auth.validate_session(raw_token)
    if user:
        return user

    raise HTTPException(status_code=403, detail="Invalid or expired token")


async def get_current_user_id(
    user: User = Depends(get_current_user),
) -> str:
    """Convenience dependency that returns just the user_id string."""
    from src.models.ids import validate_user_id

    if not validate_user_id(user.user_id):
        raise HTTPException(
            status_code=403,
            detail="Invalid user_id format",
        )
    return user.user_id


async def get_current_workspace_id(
    user: User = Depends(get_current_user),
) -> str:
    """Return the workspace_id from the authenticated session.

    Zero extra DB queries — workspace_id is set on the User during
    validate_session() from the Session record.
    """
    workspace_id = getattr(user, "_workspace_id", None)
    if not workspace_id:
        raise HTTPException(status_code=403, detail="No workspace found for user")
    return workspace_id


async def get_session(db: AsyncSession = Depends(get_db)) -> AsyncSession:
    return db


async def resolve_workspace_id(db: AsyncSession, user_id: str) -> str:
    """Resolve workspace_id from user_id for background services.

    API routes should use get_current_workspace_id() instead (zero extra queries).
    This is for scheduler, worker, perception, and other non-request code paths.
    """
    from sqlalchemy import select

    from src.models.users import WorkspaceMember

    result = await db.execute(
        select(WorkspaceMember.workspace_id)
        .where(WorkspaceMember.user_id == user_id, WorkspaceMember.role == "owner")
        .limit(1)
    )
    ws_id = result.scalar_one_or_none()
    if not ws_id:
        raise ValueError(f"No workspace found for user {user_id}")
    return ws_id
