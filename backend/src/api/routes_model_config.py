"""Model + provider configuration API (workspace-scoped). Top-level /v1 paths to
avoid the /v1/settings/{category}/{key} catch-all in routes_settings.py."""

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_current_workspace_id, get_session
from src.config import secret_crypto
from src.config.model_catalog import MODEL_CATALOG, get_model_spec
from src.config.provider_catalog import PROVIDER_CATALOG, AuthKind, FieldKind
from src.contracts.model_config import ModelBindingDTO, ModelConfigResponse, ProviderStatus
from src.llm.model_factory import build_langchain_model
from src.models.provider_credential import ProviderCredential
from src.orchestrator.agents import AGENT_MODEL_TIERS
from src.services.model_config_service import ModelConfigService
from src.services.model_resolver import KEYLESS_PROVIDERS, ModelResolver, ResolvedModel

router = APIRouter()
logger = logging.getLogger(__name__)


class CatalogModel(BaseModel):
    model_config = ConfigDict(extra="ignore", protected_namespaces=())
    # Flat: every model names its own provider, so a client filters one list rather
    # than walking a dict of lists. At 15+ providers that is the difference between
    # one search box and a nested traversal.
    provider: str
    model_id: str
    display_name: str
    thinking_style: str
    accepts_temperature: bool
    suggested_tier: str
    context_window: int
    input_cost_per_1k: float
    output_cost_per_1k: float
    supports_prompt_cache: bool


class CredentialFieldModel(BaseModel):
    model_config = ConfigDict(extra="ignore")
    key: str
    label: str
    kind: FieldKind
    required: bool
    placeholder: str | None = None


class CatalogProvider(BaseModel):
    model_config = ConfigDict(extra="ignore")
    provider: str
    display_name: str
    auth_kind: AuthKind
    credential_fields: list[CredentialFieldModel]
    model_count: int
    docs_url: str | None = None


class AgentInfo(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str
    display_name: str
    tier: str  # the agent's default reasoning tier (fallback when no override exists)


class CatalogResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    providers: list[CatalogProvider]
    models: list[CatalogModel]
    agents: list[AgentInfo]


@router.get("/v1/model-catalog", response_model=CatalogResponse)
async def get_model_catalog(workspace_id: str = Depends(get_current_workspace_id)):
    return CatalogResponse(
        # providers and models are built from separate hand-authored catalogs and
        # never filtered against each other here;
        # tests/test_provider_catalog.py::test_every_catalogued_provider_has_a_spec
        # pins set(PROVIDER_CATALOG) == set(MODEL_CATALOG), which is what keeps them
        # in agreement.
        providers=[
            CatalogProvider(
                provider=name,
                display_name=spec.display_name,
                auth_kind=spec.auth_kind,
                credential_fields=[
                    CredentialFieldModel(
                        key=f.key,
                        label=f.label,
                        kind=f.kind,
                        required=f.required,
                        placeholder=f.placeholder,
                    )
                    for f in spec.credential_fields
                ],
                model_count=len(MODEL_CATALOG.get(name, [])),
                docs_url=spec.docs_url,
            )
            for name, spec in PROVIDER_CATALOG.items()
        ],
        models=[
            CatalogModel(
                provider=s.provider,
                model_id=s.model_id,
                display_name=s.display_name,
                thinking_style=s.thinking_style,
                accepts_temperature=s.accepts_temperature,
                suggested_tier=s.suggested_tier,
                context_window=s.context_window,
                input_cost_per_1k=s.input_cost_per_1k,
                output_cost_per_1k=s.output_cost_per_1k,
                supports_prompt_cache=s.supports_prompt_cache,
            )
            for specs in MODEL_CATALOG.values()
            for s in specs
        ],
        # The agent roster and default tiers are code facts, like the model catalog,
        # so a client can offer per-agent override creation seeded from the tier.
        agents=[
            AgentInfo(name=name, display_name=name.title(), tier=tier)
            for name, tier in AGENT_MODEL_TIERS.items()
        ],
    )


class ModelConfigBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    tiers: list[ModelBindingDTO] = []
    agent_overrides: list[ModelBindingDTO] = []


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
    # A tiers[] entry declaring scope_type="agent" would write a tier row from an agent
    # DTO. The list a binding arrives in and the scope it declares must agree.
    for b in body.tiers:
        if b.scope_type != "tier":
            raise HTTPException(
                status_code=422,
                detail=f"scope_type must be 'tier' in tiers[]; got {b.scope_type!r}",
            )
    for b in body.agent_overrides:
        if b.scope_type != "agent":
            raise HTTPException(
                status_code=422,
                detail=f"scope_type must be 'agent' in agent_overrides[]; got {b.scope_type!r}",
            )
    for b in [*body.tiers, *body.agent_overrides]:
        if get_model_spec(b.provider, b.model_id) is None:
            raise HTTPException(status_code=400, detail=f"unknown model {b.provider}/{b.model_id}")
    await ModelConfigService(db).put_config(workspace_id, body.tiers, body.agent_overrides)
    await db.commit()
    return await ModelConfigService(db).get_config_response(workspace_id)


class CredentialBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    # Optional: local providers like ollama authenticate with a base_url alone (no key).
    api_key: str | None = None
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
    if not body.api_key and provider not in KEYLESS_PROVIDERS:
        raise HTTPException(status_code=400, detail=f"api_key is required for provider {provider}")
    # Keyless providers (ollama) store no ciphertext — only the base_url configures them.
    encrypted = secret_crypto.encrypt_secret(body.api_key) if body.api_key else None

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
    # Never echo the key — only the write-only status envelope. The row we just wrote
    # is this workspace's own, so it is the deletable source by construction.
    return ProviderStatus(provider=provider, configured=True, status="untested", source="workspace")


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
    # Report the state that ACTUALLY remains, not an assumed "unconfigured". Deleting
    # the workspace row can leave the provider still configured via the NULL-workspace
    # default row or the env fallback key — claiming otherwise made the UI show a
    # provider as removed until the next refetch flipped it back.
    statuses = await ModelConfigService(db).provider_statuses(workspace_id)
    return next(
        (s for s in statuses if s.provider == provider),
        ProviderStatus(provider=provider, configured=False, status="unconfigured", source="none"),
    )


@router.post("/v1/providers/{provider}/test", response_model=TestResult)
async def test_provider_credential(
    provider: str,
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_session),
):
    _require_known_provider(provider)

    # Resolve the credential exactly as ModelResolver does — workspace row, else the
    # NULL-default row, else the per-provider env fallback key — so /test agrees with
    # what GET /model-config reports as configured (F4). Keyless providers (ollama)
    # authenticate via base_url and legitimately have no api_key.
    api_key, base_url = await ModelResolver(db).resolve_credential(provider, workspace_id)
    if api_key is None and provider not in KEYLESS_PROVIDERS:
        return TestResult(status="invalid", detail="not configured")

    try:
        resolved = ResolvedModel(
            provider=provider,
            model_id=_cheap_model_id(provider),
            api_key=api_key,
            base_url=base_url,
            kwargs={"max_tokens": 1},
        )
        model = build_langchain_model(resolved)
        await model.ainvoke("ping")
        new_status, detail = "valid", None
    except Exception:  # noqa: BLE001 — fail closed: any error is an invalid credential
        logger.warning("Provider credential test failed for %s", provider, exc_info=True)
        new_status, detail = "invalid", "credential invalid"

    # Persist status only to the workspace's own credential row. Env-backed or
    # deployment-default providers have no workspace row to update — the result is
    # returned to the caller without being cached.
    ws_row = (
        (
            await db.execute(
                select(ProviderCredential).where(
                    ProviderCredential.workspace_id == workspace_id,
                    ProviderCredential.provider == provider,
                )
            )
        )
        .scalars()
        .first()
    )
    if ws_row is not None:
        ws_row.status = new_status
        await db.commit()
    return TestResult(status=new_status, detail=detail)
