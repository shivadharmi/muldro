"""Authenticated user profile route.

Extracted from routes_auth.py (decomposition, 2026-06-20)."""

from fastapi import APIRouter, Depends

from src.api.deps import get_current_user
from src.api.routes_auth_schemas import UserProfileResponse
from src.models.users import User

router = APIRouter()


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
