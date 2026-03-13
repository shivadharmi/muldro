"""FastAPI dependency injection."""

from fastapi import Depends, Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.settings import Settings, get_settings
from src.models.database import get_db


async def get_current_user(
    authorization: str | None = Header(None),
    settings: Settings = Depends(get_settings),
) -> str:
    """Validate the request comes from a trusted source (OpenClaw plugin or internal).

    For v1, single-user: just verify the backend token matches.
    Returns the hardcoded user_id for now.
    """
    if settings.backend_token:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="Missing authorization")
        token = authorization.removeprefix("Bearer ")
        if token != settings.backend_token:
            raise HTTPException(status_code=403, detail="Invalid token")
    return "usr_default"


async def get_session(db: AsyncSession = Depends(get_db)) -> AsyncSession:
    return db
