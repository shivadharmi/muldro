"""Shared Pydantic request/response models for the auth API routes.

Extracted from routes_auth.py (decomposition, 2026-06-20). Imported by the
router sub-modules (routes_auth_magic_link/oauth/session/profile)."""

from pydantic import BaseModel


class MagicLinkRequest(BaseModel):
    email: str


class MagicLinkResponse(BaseModel):
    status: str
    message: str
    token: str | None = None  # Only returned in dev mode (no backend_token set)


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


class RefreshRequest(BaseModel):
    refresh_token: str
