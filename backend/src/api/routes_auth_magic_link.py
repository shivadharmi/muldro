"""Magic-link sign-in routes (send + verify).

Extracted from routes_auth.py (decomposition, 2026-06-20)."""

import logging
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_session
from src.api.routes_auth_schemas import (
    AuthTokenResponse,
    MagicLinkRequest,
    MagicLinkResponse,
    VerifyRequest,
)
from src.config.settings import Settings, get_settings
from src.errors import ValidationError
from src.middleware.security import RATE_LIMIT_AUTH_VERIFY, per_endpoint_rate_limit
from src.services.auth_service import AuthService

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post(
    "/v1/auth/magic-link",
    response_model=MagicLinkResponse,
    dependencies=[Depends(per_endpoint_rate_limit(5))],
)
async def send_magic_link(
    req: MagicLinkRequest,
    db: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
):
    """Send a magic link to the provided email."""
    auth = AuthService(settings, db)
    token = await auth.send_magic_link(req.email)
    logger.info("Magic link generated for %s (token length=%d)", req.email, len(token))

    # In dev mode (no backend_token set), return the token so the user can verify
    if not settings.backend_token:
        return MagicLinkResponse(
            status="sent",
            message="Dev mode: use the token below to verify.",
            token=token,
        )

    # Production: send magic link email via SES
    from src.services.email_sender import EmailSender
    from src.services.email_templates import magic_link_email

    verify_url = f"{settings.frontend_url.rstrip('/')}/login?token={quote(token, safe='')}"
    body_html, body_text = magic_link_email(verify_url, settings.magic_link_ttl_minutes)

    try:
        sender = EmailSender(settings)
        await sender.send(
            to=req.email,
            subject="Sign in to Muldro",
            body_html=body_html,
            body_text=body_text,
        )
    except Exception as e:
        logger.error("Failed to send magic link email to %s: %s", req.email, e)
        raise HTTPException(status_code=500, detail="Failed to send verification email") from e

    return MagicLinkResponse(
        status="sent",
        message="Magic link sent to your email. Check your inbox.",
    )


@router.post(
    "/v1/auth/verify",
    response_model=AuthTokenResponse,
    dependencies=[Depends(per_endpoint_rate_limit(RATE_LIMIT_AUTH_VERIFY))],
)
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
        raise ValidationError(
            internal_message=str(e),
            safe_message="This sign-in link is invalid or has expired.",
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
