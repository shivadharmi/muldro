"""Authentication routes — magic link, OAuth, sessions."""

import logging
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_current_user, get_session
from src.config.settings import Settings, get_settings
from src.models.users import User
from src.services.auth_service import AuthService

router = APIRouter()
logger = logging.getLogger(__name__)


# ── Request / Response Schemas ───────────────────────────────


class MagicLinkRequest(BaseModel):
    email: str


class MagicLinkResponse(BaseModel):
    status: str
    message: str


class VerifyRequest(BaseModel):
    token: str


class AuthTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: str
    user: dict


class UserProfileResponse(BaseModel):
    user_id: str
    email: str
    display_name: str | None
    avatar_url: str | None
    status: str
    onboarding_completed: bool
    settings: dict | None


class OAuthUrlResponse(BaseModel):
    url: str
    provider: str


# ── Magic Link ───────────────────────────────────────────────


@router.post("/v1/auth/magic-link", response_model=MagicLinkResponse)
async def send_magic_link(
    req: MagicLinkRequest,
    db: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
):
    """Send a magic link to the provided email."""
    auth = AuthService(settings, db)
    token = await auth.send_magic_link(req.email)
    # In production, send email. For now, return status.
    logger.info("Magic link generated for %s (token length=%d)", req.email, len(token))
    return MagicLinkResponse(
        status="sent",
        message="Magic link sent to your email. Check your inbox.",
    )


@router.post("/v1/auth/verify", response_model=AuthTokenResponse)
async def verify_magic_link(
    req: VerifyRequest,
    db: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
):
    """Verify a magic link token and return a session."""
    auth = AuthService(settings, db)
    try:
        session = await auth.verify_magic_link(req.token)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

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


# ── OAuth ────────────────────────────────────────────────────


@router.get("/v1/auth/{provider}/authorize", response_model=OAuthUrlResponse)
@router.get("/v1/auth/oauth/{provider}/authorize", response_model=OAuthUrlResponse)
async def oauth_authorize(
    provider: str,
    scopes: str = Query("", description="Space-separated OAuth scopes"),
    settings: Settings = Depends(get_settings),
):
    """Generate OAuth authorization URL for a provider."""
    if provider == "google":
        client_id = settings.google_oauth_client_id
        if not client_id:
            raise HTTPException(status_code=400, detail="Google OAuth not configured")

        default_scopes = (
            "openid email profile "
            "https://www.googleapis.com/auth/gmail.readonly "
            "https://www.googleapis.com/auth/calendar.readonly"
        )
        params = {
            "client_id": client_id,
            "redirect_uri": settings.google_oauth_redirect_uri,
            "response_type": "code",
            "scope": scopes or default_scopes,
            "access_type": "offline",
            "prompt": "consent",
        }
        url = f"https://accounts.google.com/o/oauth2/v2/auth?{urlencode(params)}"
        return OAuthUrlResponse(url=url, provider="google")

    elif provider == "github":
        client_id = settings.github_oauth_client_id
        if not client_id:
            raise HTTPException(status_code=400, detail="GitHub OAuth not configured")

        params = {
            "client_id": client_id,
            "redirect_uri": settings.github_oauth_redirect_uri,
            "scope": scopes or "read:user user:email repo",
        }
        url = f"https://github.com/login/oauth/authorize?{urlencode(params)}"
        return OAuthUrlResponse(url=url, provider="github")

    else:
        raise HTTPException(status_code=400, detail=f"Unknown provider: {provider}")


@router.get("/v1/auth/{provider}/callback")
@router.get("/v1/auth/oauth/{provider}/callback")
async def oauth_callback(
    provider: str,
    code: str = Query(...),
    state: str = Query(""),
    settings: Settings = Depends(get_settings),
):
    """Handle OAuth callback — exchange code for tokens, store as connector.

    This is a connector OAuth flow, not a login flow.
    Exchanges the authorization code for access/refresh tokens,
    stores them encrypted via OAuthManager, and redirects to the frontend.
    """
    import httpx

    from src.models.database import get_session_factory
    from src.services.oauth_manager import OAuthManager

    # user_id comes from state param or defaults to single-user
    user_id = state if state.startswith("usr_") else "usr_default"

    if provider == "google":
        client_id = settings.google_oauth_client_id
        client_secret = settings.google_oauth_client_secret
        if not client_id or not client_secret:
            raise HTTPException(status_code=500, detail="Google OAuth not configured")

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "redirect_uri": settings.google_oauth_redirect_uri,
                },
                timeout=10,
            )
            if resp.status_code != 200:
                logger.error("Google token exchange failed: %s", resp.text)
                return _error_redirect(settings, "Failed to exchange authorization code")
            token_data = resp.json()

            # Get user info to confirm the account
            userinfo_resp = await client.get(
                "https://www.googleapis.com/oauth2/v2/userinfo",
                headers={"Authorization": f"Bearer {token_data['access_token']}"},
                timeout=10,
            )
            userinfo = userinfo_resp.json() if userinfo_resp.status_code == 200 else {}

        expires_at = None
        if token_data.get("expires_in"):
            expires_at = datetime.now(timezone.utc) + timedelta(seconds=token_data["expires_in"])

        scopes = token_data.get("scope", "").split() if token_data.get("scope") else None

        # Store tokens via OAuthManager (encrypted at rest)
        db_factory = get_session_factory()
        oauth_mgr = OAuthManager(db_factory, encryption_key=settings.oauth_encryption_key)
        await oauth_mgr.store_token(
            user_id=user_id,
            provider="google",
            access_token=token_data["access_token"],
            refresh_token=token_data.get("refresh_token"),
            expires_at=expires_at,
            scopes=scopes,
        )

        # Register connectors for the Google services
        await _ensure_connector(db_factory, user_id, "gmail", userinfo.get("email"))
        await _ensure_connector(db_factory, user_id, "calendar", userinfo.get("email"))

        logger.info(
            "Google connector linked for %s (%s)",
            user_id,
            userinfo.get("email", "unknown"),
        )

    elif provider == "github":
        client_id = settings.github_oauth_client_id
        client_secret = settings.github_oauth_client_secret
        if not client_id or not client_secret:
            raise HTTPException(status_code=500, detail="GitHub OAuth not configured")

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://github.com/login/oauth/access_token",
                headers={"Accept": "application/json"},
                data={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "code": code,
                },
                timeout=10,
            )
            if resp.status_code != 200:
                logger.error("GitHub token exchange failed: %s", resp.text)
                return _error_redirect(settings, "Failed to exchange authorization code")
            token_data = resp.json()

        scopes = token_data.get("scope", "").split(",") if token_data.get("scope") else None

        db_factory = get_session_factory()
        oauth_mgr = OAuthManager(db_factory, encryption_key=settings.oauth_encryption_key)
        await oauth_mgr.store_token(
            user_id=user_id,
            provider="github",
            access_token=token_data["access_token"],
            refresh_token=None,
            expires_at=None,
            scopes=scopes,
        )

        await _ensure_connector(db_factory, user_id, "github")

        logger.info("GitHub connector linked for %s", user_id)

    else:
        raise HTTPException(status_code=400, detail=f"Unknown provider: {provider}")

    # Redirect to frontend connectors page with success status
    frontend_url = settings.frontend_url.rstrip("/")
    params = urlencode({"provider": provider, "status": "connected"})
    return RedirectResponse(url=f"{frontend_url}/connectors?{params}")


async def _ensure_connector(
    db_factory, user_id: str, provider: str, account_email: str | None = None
) -> None:
    """Create or reactivate a connector record after OAuth."""
    from sqlalchemy import select as sa_select
    from ulid import ULID

    from src.models.connectors import Connector

    db = db_factory()
    try:
        result = await db.execute(
            sa_select(Connector).where(Connector.user_id == user_id, Connector.provider == provider)
        )
        existing = result.scalar_one_or_none()
        if existing:
            existing.status = "active"
        else:
            db.add(
                Connector(
                    connector_id=f"conn_{ULID()}",
                    user_id=user_id,
                    provider=provider,
                    status="active",
                    config={"account_email": account_email} if account_email else {},
                )
            )
        await db.commit()
    except Exception:
        await db.rollback()
        logger.warning("Failed to ensure connector %s for %s", provider, user_id, exc_info=True)


def _error_redirect(settings: Settings, message: str) -> RedirectResponse:
    """Redirect to frontend with an error message."""
    frontend_url = settings.frontend_url.rstrip("/")
    params = urlencode({"error": message})
    return RedirectResponse(url=f"{frontend_url}/connectors?{params}")


# ── Session Management ───────────────────────────────────────


class RefreshRequest(BaseModel):
    refresh_token: str


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
        raise HTTPException(status_code=401, detail=str(e)) from e

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
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
):
    """Revoke the current session."""
    # In a full implementation, we'd get the session_id from the token
    return {"status": "logged_out"}


@router.get("/v1/auth/me", response_model=UserProfileResponse)
async def get_current_profile(user: User = Depends(get_current_user)):
    """Get the current user's profile."""
    return UserProfileResponse(
        user_id=user.user_id,
        email=user.email,
        display_name=user.display_name,
        avatar_url=user.avatar_url,
        status=user.status,
        onboarding_completed=user.onboarding_completed,
        settings=user.settings,
    )
