"""Session lifecycle routes: token refresh and logout.

Extracted from routes_auth.py (decomposition, 2026-06-20)."""

import logging

from fastapi import APIRouter, Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_session
from src.api.routes_auth_schemas import (
    AuthTokenResponse,
    RefreshRequest,
)
from src.config.settings import Settings, get_settings
from src.errors import AuthError
from src.services.auth_service import AuthService

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/v1/auth/refresh", response_model=AuthTokenResponse)
async def refresh_token(
    req: RefreshRequest,
    db: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
):
    """Refresh an expired session token."""
    auth = AuthService(settings, db)
    try:
        session = await auth.refresh_session(req.refresh_token)
    except ValueError as e:
        raise AuthError(
            internal_message=str(e),
            safe_message="Your session could not be refreshed. Please sign in again.",
        ) from e

    user = await auth.get_user(session.user_id)
    raw_token = session._raw_token  # type: ignore[attr-defined]

    return AuthTokenResponse(
        access_token=raw_token,
        expires_at=session.expires_at.isoformat(),
        user={
            "user_id": user.user_id,
            "email": user.email,
            "display_name": user.display_name,
        },
    )


@router.post("/v1/auth/logout")
async def logout(
    authorization: str | None = Header(None),
    db: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
):
    """Revoke the current session by looking up the token hash."""
    if not authorization or not authorization.startswith("Bearer "):
        return {"status": "logged_out"}

    raw_token = authorization.removeprefix("Bearer ")

    # Don't try to revoke the legacy backend_token
    if settings.backend_token and raw_token == settings.backend_token:
        return {"status": "logged_out"}

    auth = AuthService(settings, db)
    import hashlib

    from sqlalchemy import select as sa_select

    from src.models.users import Session as UserSession

    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    result = await db.execute(
        sa_select(UserSession).where(
            UserSession.token_hash == token_hash,
            UserSession.revoked_at.is_(None),
        )
    )
    session = result.scalar_one_or_none()
    if session:
        await auth.revoke_session(session.session_id)

    return {"status": "logged_out"}
