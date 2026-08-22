"""Model + provider configuration API (workspace-scoped). Top-level /v1 paths to
avoid the /v1/settings/{category}/{key} catch-all in routes_settings.py."""

import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_current_workspace_id, get_session
from src.config import secret_crypto
from src.config.model_catalog import MODEL_CATALOG, get_model_spec
from src.config.provider_catalog import PROVIDER_CATALOG, AuthKind, FieldKind
from src.contracts.model_config import (
    ConfigWarning,
    ModelBindingDTO,
    ModelConfigResponse,
    ProviderStatus,
)
from src.llm.model_factory import build_langchain_model
from src.models.provider_credential import ProviderCredential
from src.orchestrator.agents import AGENT_MODEL_TIERS
from src.services.model_config_service import (
    ModelConfigService,
    merge_extra_config,
    unknown_extra_keys,
)
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
    # None means "leave agent overrides alone"; [] means "clear them all". Overrides use
    # REPLACE semantics, so collapsing absent into empty would let a tiers-only PUT
    # silently delete every override in the workspace.
    agent_overrides: list[ModelBindingDTO] | None = None


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
    for b in body.agent_overrides or []:
        if b.scope_type != "agent":
            raise HTTPException(
                status_code=422,
                detail=f"scope_type must be 'agent' in agent_overrides[]; got {b.scope_type!r}",
            )
    for b in [*body.tiers, *(body.agent_overrides or [])]:
        if get_model_spec(b.provider, b.model_id) is None:
            raise HTTPException(status_code=400, detail=f"unknown model {b.provider}/{b.model_id}")

    service = ModelConfigService(db)
    # A tier has no fallback: ModelResolver raises for it, and the run dies. Reject the
    # bind rather than accept a config that cannot run. Agent overrides degrade to their
    # tier binding (model_resolver.py:70), so they are warned about, not refused.
    blocking = await service.unconfigured_bindings(workspace_id, body.tiers, [])
    if blocking:
        # A plain `raise HTTPException(detail=[...])` would lose this structure: the
        # central handler (error_handlers.py) collapses any non-string `.detail` into
        # the generic {"error": {"message": "Request failed."}} envelope, by design --
        # every other error in this file uses a string detail for exactly that reason.
        # This is the one response that must carry which bindings are blocking (so a
        # client can point at them), so it returns directly and bypasses that handler.
        return JSONResponse(status_code=422, content={"detail": [w.model_dump() for w in blocking]})

    await service.put_config(workspace_id, body.tiers, body.agent_overrides)
    await db.commit()
    return await ModelConfigService(db).get_config_response(workspace_id)


class CredentialBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    # Optional: local providers like ollama authenticate with a base_url alone (no key).
    api_key: str | None = None
    base_url: str | None = None
    extra_config: dict[str, object] | None = None


class TestResult(BaseModel):
    model_config = ConfigDict(extra="ignore")
    status: str
    detail: str | None = None


class CredentialDeleteResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    status: ProviderStatus
    # Bindings this revoke just broke. Reported AFTER the delete, so it describes the
    # state that now exists rather than one predicted before the write.
    orphaned_bindings: list[ConfigWarning] = []


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
    """Partial update. An OMITTED field is left untouched; an explicit null clears it.

    Assigning every field unconditionally meant a client that sent only the key it was
    rotating silently nulled base_url and extra_config -- and for ollama, whose
    base_url IS the credential, that unconfigured the provider outright (B1).

    The same rule holds one level down, per KEY inside extra_config -- see
    ``merge_extra_config``. Replacing that map wholesale destroyed any secret the
    client had omitted, and a client cannot resend a secret it can never read back.
    """
    _require_known_provider(provider)

    stmt = select(ProviderCredential).where(
        ProviderCredential.workspace_id == workspace_id,
        ProviderCredential.provider == provider,
    )
    existing = (await db.execute(stmt)).scalars().first()

    # api_key is required to CREATE a keyed provider's credential, not to edit one:
    # editing a base URL must not force the founder to retype a secret they cannot see.
    if existing is None and not body.api_key and provider not in KEYLESS_PROVIDERS:
        raise HTTPException(status_code=400, detail=f"api_key is required for provider {provider}")

    fields = body.model_dump(exclude_unset=True)

    # Validated BEFORE any merge. A merge is accumulative, so an undeclared key
    # written once would live in the JSONB forever, invisible and undeletable
    # through the form -- and a key shadowing a top-level field would be echoed
    # back as public alongside the real one.
    unknown = unknown_extra_keys(provider, fields.get("extra_config"))
    if unknown:
        raise HTTPException(
            status_code=400, detail=f"unknown credential fields for {provider}: {unknown}"
        )

    if existing is not None:
        # Only a REAL write invalidates a prior verification -- `status` renders in
        # the Providers tab, so downgrading a `valid` provider to `untested` for a
        # request that changed nothing is a visible lie. A merge makes that check
        # non-trivial one level down: PUT {"extra_config": {}} carries a field but
        # writes nothing, so presence in `fields` is no longer proof of a change.
        wrote = False
        if "api_key" in fields:
            existing.api_key_encrypted = (
                secret_crypto.encrypt_secret(fields["api_key"]) if fields["api_key"] else None
            )
            wrote = True
        if "base_url" in fields:
            existing.base_url = fields["base_url"]
            wrote = True
        if "extra_config" in fields:
            # Merge, never replace: an omitted key is "leave it alone", which is the
            # only way a client can keep a secret it is never allowed to read back.
            merged = merge_extra_config(existing.extra_config, fields["extra_config"])
            if merged != existing.extra_config:
                existing.extra_config = merged
                wrote = True
        if wrote:
            existing.status = "untested"
            existing.enabled = True
    else:
        api_key = fields.get("api_key")
        db.add(
            ProviderCredential(
                workspace_id=workspace_id,
                provider=provider,
                # Keyless providers (ollama) store no ciphertext -- only base_url configures them.
                api_key_encrypted=secret_crypto.encrypt_secret(api_key) if api_key else None,
                base_url=fields.get("base_url"),
                # No stored row to merge against, but the null-deletes-a-key rule
                # still applies so a create and an edit agree on what a null means.
                extra_config=merge_extra_config(None, fields.get("extra_config")),
                status="untested",
                enabled=True,
            )
        )

    await db.commit()
    # Never echo the key — only the write-only status envelope. The row we just wrote
    # is this workspace's own, so it is the deletable source by construction.
    statuses = await ModelConfigService(db).provider_statuses(workspace_id)
    return next(s for s in statuses if s.provider == provider)


@router.delete("/v1/providers/{provider}/credentials", response_model=CredentialDeleteResponse)
async def delete_provider_credential(
    provider: str,
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_session),
):
    """Revoke this workspace's credential, and report what the revoke broke.

    Never blocks: a credential the founder cannot revoke is a security problem.

    Deliberately NOT guarded by ``_require_known_provider``. ``catalogued`` is derived
    as ``provider in MODEL_CATALOG``, which is the same condition that guard rejects on
    -- so guarding here meant ``catalogued=False`` implied "undeletable", biconditionally.
    A stray row (a provider dropped from the catalog, or one whose key no longer
    decrypts) is shown precisely SO it can be removed, and Remove is the only action its
    row offers; a guard turned that into a button that could never work. PUT and /test
    keep the guard because they index MODEL_CATALOG afterwards; this handler indexes
    nothing, it only filters ProviderCredential by (workspace_id, provider).
    """
    stmt = select(ProviderCredential).where(
        ProviderCredential.workspace_id == workspace_id,
        ProviderCredential.provider == provider,
    )
    existing = (await db.execute(stmt)).scalars().first()
    if existing is not None:
        await db.delete(existing)
        await db.commit()

    service = ModelConfigService(db)
    # Report the state that ACTUALLY remains, not an assumed "unconfigured". Deleting
    # the workspace row can leave the provider still configured via the NULL-workspace
    # default row or the env fallback key — claiming otherwise made the UI show a
    # provider as removed until the next refetch flipped it back.
    config = await service.get_config_response(workspace_id)
    status = next(
        (s for s in config.providers if s.provider == provider),
        ProviderStatus(provider=provider, configured=False, status="unconfigured", source="none"),
    )
    return CredentialDeleteResponse(
        status=status,
        orphaned_bindings=[
            w
            for w in config.warnings
            # Only what THIS revoke broke. config.warnings spans every binding in the
            # workspace, so an unrelated provider that was already unconfigured would
            # otherwise be misreported as a consequence of deleting this one.
            if w.code == "provider_not_configured" and w.provider == provider
        ],
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
