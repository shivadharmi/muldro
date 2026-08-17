"""Model + provider configuration API (workspace-scoped). Top-level /v1 paths to
avoid the /v1/settings/{category}/{key} catch-all in routes_settings.py."""

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_current_workspace_id, get_session
from src.config.model_catalog import MODEL_CATALOG, get_model_spec
from src.services.model_config_service import ModelConfigService

router = APIRouter()
logger = logging.getLogger(__name__)


class CatalogModel(BaseModel):
    model_config = ConfigDict(extra="ignore", protected_namespaces=())
    model_id: str
    display_name: str
    thinking_style: str
    accepts_temperature: bool
    suggested_tier: str


class CatalogResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    providers: dict[str, list[CatalogModel]]


@router.get("/v1/model-catalog", response_model=CatalogResponse)
async def get_model_catalog(workspace_id: str = Depends(get_current_workspace_id)):
    return CatalogResponse(
        providers={
            p: [
                CatalogModel(
                    model_id=s.model_id,
                    display_name=s.display_name,
                    thinking_style=s.thinking_style,
                    accepts_temperature=s.accepts_temperature,
                    suggested_tier=s.suggested_tier,
                )
                for s in specs
            ]
            for p, specs in MODEL_CATALOG.items()
        }
    )


class TierBinding(BaseModel):
    model_config = ConfigDict(extra="ignore", protected_namespaces=())
    # For an agent override this field carries the AGENT NAME (round-tripped as the
    # scope_key of a scope_type="agent" ModelBinding); for a tier it is the tier name.
    tier: str
    provider: str
    model_id: str
    effort: str = "none"
    max_tokens: int = 4096
    temperature: float | None = None


class ModelConfigBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    tiers: list[TierBinding]
    agent_overrides: list[TierBinding] = []


class ProviderStatus(BaseModel):
    model_config = ConfigDict(extra="ignore")
    provider: str
    configured: bool
    status: str


class ModelConfigResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    tiers: list[TierBinding]
    agent_overrides: list[TierBinding]
    providers: list[ProviderStatus]


@router.get("/v1/model-config", response_model=ModelConfigResponse)
async def get_model_config(
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_session),
):
    return await ModelConfigService(db).get_config_response(workspace_id)


@router.put("/v1/model-config", response_model=ModelConfigResponse)
async def put_model_config(
    body: ModelConfigBody,
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_session),
):
    for b in [*body.tiers, *body.agent_overrides]:
        if get_model_spec(b.provider, b.model_id) is None:
            raise HTTPException(status_code=400, detail=f"unknown model {b.provider}/{b.model_id}")
    await ModelConfigService(db).put_config(workspace_id, body.tiers, body.agent_overrides)
    await db.commit()
    return await ModelConfigService(db).get_config_response(workspace_id)
