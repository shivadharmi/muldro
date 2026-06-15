"""FastAPI dependency injection."""

from fastapi import Depends, Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.settings import Settings, get_settings
from src.models.database import get_db
from src.models.users import User

# Canonical definition lives in the service layer; re-exported here so route
# handlers can keep importing it from api.deps (the API dependency surface).
from src.services.workspace_resolver import resolve_workspace_id

__all__ = ["resolve_workspace_id"]


async def get_current_user(
    authorization: str | None = Header(None),
    settings: Settings = Depends(get_settings),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Validate the request and return the authenticated User.

    Auth via Authorization: Bearer <session_token> header only.
    No query param token fallback — SSE uses fetch with headers,
    WebSocket uses auth-message-after-connect.
    """
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
