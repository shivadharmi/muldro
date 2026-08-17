"""Model + provider configuration API (workspace-scoped). Top-level /v1 paths to
avoid the /v1/settings/{category}/{key} catch-all in routes_settings.py."""

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_current_workspace_id, get_session
from src.config import secret_crypto
from src.config.model_catalog import MODEL_CATALOG, get_model_spec
from src.contracts.model_config import ModelConfigResponse, ProviderStatus, TierBinding
from src.llm.model_factory import build_langchain_model
from src.models.provider_credential import ProviderCredential
from src.services.model_config_service import ModelConfigService
from src.services.model_resolver import ResolvedModel

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


class ModelConfigBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    tiers: list[TierBinding]
    agent_overrides: list[TierBinding] = []


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


class CredentialBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    api_key: str
    base_url: str | None = None
    extra_config: dict | None = None


class TestResult(BaseModel):
    model_config = ConfigDict(extra="ignore")
    status: str
    detail: str | None = None


def _require_known_provider(provider: str) -> None:
    if provider not in MODEL_CATALOG:
        raise HTTPException(status_code=400, detail=f"unknown provider {provider}")


def _cheap_model_id(provider: str) -> str:
    """Pick the cheapest model to probe a provider: prefer the 'fast' tier, else the first."""
    specs = MODEL_CATALOG[provider]
    for spec in specs:
        if spec.suggested_tier == "fast":
            return spec.model_id
    return specs[0].model_id


@router.put("/v1/providers/{provider}/credentials", response_model=ProviderStatus)
async def put_provider_credential(
    provider: str,
    body: CredentialBody,
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_session),
):
    _require_known_provider(provider)
    encrypted = secret_crypto.encrypt_secret(body.api_key)

    stmt = select(ProviderCredential).where(
        ProviderCredential.workspace_id == workspace_id,
        ProviderCredential.provider == provider,
    )
    existing = (await db.execute(stmt)).scalars().first()
    if existing is not None:
        existing.api_key_encrypted = encrypted
        existing.base_url = body.base_url
        existing.extra_config = body.extra_config
        existing.status = "untested"
        existing.enabled = True
    else:
        db.add(
            ProviderCredential(
                workspace_id=workspace_id,
                provider=provider,
                api_key_encrypted=encrypted,
                base_url=body.base_url,
                extra_config=body.extra_config,
                status="untested",
                enabled=True,
            )
        )
    await db.commit()
    # Never echo the key — only the write-only status envelope.
    return ProviderStatus(provider=provider, configured=True, status="untested")


@router.delete("/v1/providers/{provider}/credentials", response_model=ProviderStatus)
async def delete_provider_credential(
    provider: str,
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_session),
):
    _require_known_provider(provider)
    stmt = select(ProviderCredential).where(
        ProviderCredential.workspace_id == workspace_id,
        ProviderCredential.provider == provider,
    )
    existing = (await db.execute(stmt)).scalars().first()
    if existing is not None:
        await db.delete(existing)
        await db.commit()
    return ProviderStatus(provider=provider, configured=False, status="unconfigured")


@router.post("/v1/providers/{provider}/test", response_model=TestResult)
async def test_provider_credential(
    provider: str,
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_session),
):
    _require_known_provider(provider)

    # Prefer the workspace credential, else the deployment-default (NULL) row.
    stmt = (
        select(ProviderCredential)
        .where(
            ProviderCredential.provider == provider,
            or_(
                ProviderCredential.workspace_id == workspace_id,
                ProviderCredential.workspace_id.is_(None),
            ),
        )
        .order_by(ProviderCredential.workspace_id.is_(None))
    )
    row = (await db.execute(stmt)).scalars().first()
    if row is None or not row.api_key_encrypted:
        return TestResult(status="invalid", detail="not configured")

    try:
        api_key = secret_crypto.decrypt_secret(row.api_key_encrypted)
        resolved = ResolvedModel(
            provider=provider,
            model_id=_cheap_model_id(provider),
            api_key=api_key,
            base_url=row.base_url,
            kwargs={"max_tokens": 1},
        )
        model = build_langchain_model(resolved)
        await model.ainvoke("ping")
        new_status, detail = "valid", None
    except Exception as e:  # noqa: BLE001 — fail closed: any error is an invalid credential
        new_status, detail = "invalid", str(e)[:200]

    # row may be a NULL-default row; only persist status when it is our workspace row.
    if row.workspace_id == workspace_id:
        row.status = new_status
        await db.commit()
    else:
        await db.rollback()
    return TestResult(status=new_status, detail=detail)
