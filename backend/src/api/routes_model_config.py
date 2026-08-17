"""Model + provider configuration API (workspace-scoped). Top-level /v1 paths to
avoid the /v1/settings/{category}/{key} catch-all in routes_settings.py."""

import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict

from src.api.deps import get_current_workspace_id
from src.config.model_catalog import MODEL_CATALOG

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
