"""Authentication routes — magic link, OAuth, sessions."""

from fastapi import APIRouter

from src.api.routes_auth_magic_link import router as magic_link_router
from src.api.routes_auth_oauth import router as oauth_router
from src.api.routes_auth_profile import router as profile_router
from src.api.routes_auth_session import router as session_router

router = APIRouter()

# ── Aggregated sub-routers ───────────────────────────────────
router.include_router(magic_link_router)
router.include_router(oauth_router)
router.include_router(session_router)
router.include_router(profile_router)
