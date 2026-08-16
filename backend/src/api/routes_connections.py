"""Connect-account endpoints — begin OAuth authorization + confirm activation."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_current_user_id, get_current_workspace_id
from src.models.database import get_db
from src.services.connection_service import ConnectionService

router = APIRouter(prefix="/v1/connections", tags=["connections"])


class BeginConnectionRequest(BaseModel):
    provider: str
    alias: str = "default"


class BeginConnectionResponse(BaseModel):
    authorization_url: str


class ConfirmConnectionResponse(BaseModel):
    status: str  # "active" | "pending"


@router.post("/begin", response_model=BeginConnectionResponse)
async def begin(
    body: BeginConnectionRequest,
    workspace_id: str = Depends(get_current_workspace_id),
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
) -> BeginConnectionResponse:
    svc = ConnectionService()
    url = await svc.begin_connection(
        db,
        workspace_id=workspace_id,
        principal_id=user_id,
        provider=body.provider,
        alias=body.alias,
    )
    await db.commit()
    return BeginConnectionResponse(authorization_url=url)


@router.post("/confirm", response_model=ConfirmConnectionResponse)
async def confirm(
    body: BeginConnectionRequest,
    workspace_id: str = Depends(get_current_workspace_id),
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
) -> ConfirmConnectionResponse:
    svc = ConnectionService()
    active = await svc.confirm_connection(
        db,
        workspace_id=workspace_id,
        principal_id=user_id,
        provider=body.provider,
        alias=body.alias,
    )
    await db.commit()
    return ConfirmConnectionResponse(status="active" if active else "pending")
