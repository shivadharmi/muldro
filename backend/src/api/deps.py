"""FastAPI dependency injection."""

from fastapi import Depends, Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.settings import Settings, get_settings
from src.models.database import get_db
from src.models.users import User


async def get_current_user(
    authorization: str | None = Header(None),
    settings: Settings = Depends(get_settings),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Validate the request and return the authenticated User.

    Supports two modes:
    1. Session token: Bearer <session_token> (multi-user)
    2. Legacy backend token: Bearer <backend_token> (backward compat, returns usr_default)
    """
    if not authorization or not authorization.startswith("Bearer "):
        # Allow unauthenticated access if no backend_token configured (dev mode)
        if not settings.backend_token:
            return await _get_default_user(db)
        raise HTTPException(status_code=401, detail="Missing authorization")

    token = authorization.removeprefix("Bearer ")

    # Check legacy backend token first (backward compat)
    if settings.backend_token and token == settings.backend_token:
        return await _get_default_user(db)

    # Try session-based auth
    from src.services.auth_service import AuthService

    auth = AuthService(settings, db)
    user = await auth.validate_session(token)
    if user:
        return user

    raise HTTPException(status_code=403, detail="Invalid or expired token")


async def get_current_user_id(
    user: User = Depends(get_current_user),
) -> str:
    """Convenience dependency that returns just the user_id string."""
    return user.user_id


async def get_session(db: AsyncSession = Depends(get_db)) -> AsyncSession:
    return db


async def _get_default_user(db: AsyncSession) -> User:
    """Get or create the default user for backward compatibility."""
    from sqlalchemy import select

    result = await db.execute(select(User).where(User.user_id == "usr_default"))
    user = result.scalar_one_or_none()
    if user:
        return user

    # Create inline if migration hasn't run yet
    user = User(
        user_id="usr_default",
        email="admin@jarvis.local",
        display_name="Default User",
        status="active",
        onboarding_completed=True,
        settings={},
    )
    db.add(user)
    await db.flush()
    return user
