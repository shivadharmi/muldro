"""API routes for the integration platform.

Endpoints:
  GET    /v1/integrations                — list installations
  POST   /v1/integrations                — create installation
  GET    /v1/integrations/{id}           — get installation
  DELETE /v1/integrations/{id}           — delete installation
  POST   /v1/integrations/{id}/pause     — pause installation
  POST   /v1/integrations/{id}/resume    — resume installation
  GET    /v1/integrations/{id}/health    — check health
  GET    /v1/integrations/capabilities   — list capability bindings
  GET    /v1/integrations/trust          — list trust records
  GET    /v1/integrations/health         — check all health
"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_current_user_id, get_current_workspace_id
from src.models.database import get_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/integrations")


# ── Request/Response schemas ─────────────────────────────────────────


class InstallationResponse(BaseModel):
    install_id: str
    server_name: str
    display_name: str
    transport: str
    status: str
    health_status: str
    enabled: bool
    auth_provider: str | None = None
    scopes_granted: list[str] | None = None
    created_at: str | None = None

    model_config = {"from_attributes": True}


class CreateInstallationRequest(BaseModel):
    server_name: str
    display_name: str
    transport: str = "stdio"
    command: str | None = None
    args: list | None = None
    env_template: dict | None = None
    remote_url: str | None = None
    auth_provider: str | None = None
    scopes_granted: list[str] | None = None
    config: dict | None = None


class TrustRecordResponse(BaseModel):
    trust_id: str
    server_name: str
    trust_tier: str
    verified_by: str | None = None
    status: str
    created_at: str | None = None

    model_config = {"from_attributes": True}


class HealthCheckResponse(BaseModel):
    install_id: str | None = None
    server_name: str | None = None
    health_status: str


class UnifiedIntegrationResponse(BaseModel):
    server_name: str
    display_name: str
    provider: str | None = None
    category: str  # "oauth", "token", "local"
    configured: bool
    connected: bool
    health_status: str
    enabled: bool
    install_id: str | None = None
    scopes: list[str] = Field(default_factory=list)
    # Stable lowercase brand key for logo asset lookup (e.g. "google", "github").
    slug: str = ""
    # Coarse access level derived from `scopes`: subset of ["read", "write"].
    access_scopes: list[str] = Field(default_factory=list)
    # True when an OAuth integration is configured but its token is permanently
    # unusable — the user must reconnect (gates the "Reconnect" UI affordance).
    needs_reauth: bool = False
    # Every OC provider this installation serves, in registry order (empty for
    # non-gateway installations) — tells the frontend which connect flow to use.
    oc_providers: list[str] = Field(default_factory=list)
    # Per-provider connectivity from `connection_map` (empty for non-gateway
    # installations), so a partially connected installation stays visible
    # instead of collapsing into one "disconnected".
    provider_connections: dict[str, bool] = Field(default_factory=dict)
    # Registry `display_name` per OC provider (empty for non-gateway
    # installations) — the frontend renders these instead of hand-maintaining
    # its own provider -> label table that degrades to a raw slug.
    oc_provider_labels: dict[str, str] = Field(default_factory=dict)
    # The second credential a dual-credential installation holds — gateway-backed
    # for its actions AND its own OAuth token for a poll the gateway cannot
    # serve. None on every single-credential installation; when set, the card
    # renders a chip and a separate connect button for it.
    native_provider: str | None = None
    native_connected: bool = False
    # Short human phrase for what that token buys ("notifications").
    native_purpose: str = ""


# ── Endpoints ────────────────────────────────────────────────────────


@router.get("", response_model=list[InstallationResponse])
async def list_installations(
    status: str | None = None,
    enabled_only: bool = False,
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_db),
):
    from src.integrations.control_plane import IntegrationControlPlane

    cp = IntegrationControlPlane(db, workspace_id)
    installations = await cp.list_installations(status=status, enabled_only=enabled_only)
    return [
        InstallationResponse(
            install_id=i.install_id,
            server_name=i.server_name,
            display_name=i.display_name,
            transport=i.transport,
            status=i.status,
            health_status=i.health_status,
            enabled=i.enabled,
            auth_provider=i.auth_provider,
            scopes_granted=i.scopes_granted,
            created_at=i.created_at.isoformat() if i.created_at else None,
        )
        for i in installations
    ]


@router.get("/unified", response_model=list[UnifiedIntegrationResponse])
async def list_unified_integrations(
    user_id: str = Depends(get_current_user_id),
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_db),
):
    """Unified view: joins MCP installations with OAuth provider status."""
    from src.integrations.gateway_actions import provider_labels_for_server
    from src.services.integration_status import get_integration_statuses

    statuses = await get_integration_statuses(db, user_id, workspace_id)
    return [
        UnifiedIntegrationResponse(
            server_name=s.server_name,
            display_name=s.display_name,
            provider=s.provider,
            category=s.category,
            configured=s.configured,
            connected=s.connected,
            health_status=s.health_status,
            enabled=s.enabled,
            install_id=s.install_id,
            scopes=s.scopes,
            slug=s.slug,
            access_scopes=s.access_scopes,
            needs_reauth=s.needs_reauth,
            oc_providers=s.oc_providers,
            provider_connections=s.provider_connections,
            oc_provider_labels=provider_labels_for_server(s.server_name),
            native_provider=s.native_provider,
            native_connected=s.native_connected,
            native_purpose=s.native_purpose,
        )
        for s in statuses
    ]


@router.post("", response_model=InstallationResponse, status_code=201)
async def create_installation(
    body: CreateInstallationRequest,
    user_id: str = Depends(get_current_user_id),
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_db),
):
    from src.integrations.control_plane import IntegrationControlPlane

    cp = IntegrationControlPlane(db, workspace_id)

    existing = await cp.get_by_server_name(body.server_name)
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"Installation '{body.server_name}' already exists",
        )

    inst = await cp.create_installation(
        user_id=user_id,
        server_name=body.server_name,
        display_name=body.display_name,
        transport=body.transport,
        command=body.command,
        args=body.args,
        env_template=body.env_template,
        remote_url=body.remote_url,
        auth_provider=body.auth_provider,
        scopes_granted=body.scopes_granted,
        config=body.config,
    )
    await db.commit()
    return InstallationResponse(
        install_id=inst.install_id,
        server_name=inst.server_name,
        display_name=inst.display_name,
        transport=inst.transport,
        status=inst.status,
        health_status=inst.health_status,
        enabled=inst.enabled,
        auth_provider=inst.auth_provider,
        scopes_granted=inst.scopes_granted,
        created_at=inst.created_at.isoformat() if inst.created_at else None,
    )


@router.get("/{install_id}", response_model=InstallationResponse)
async def get_installation(
    install_id: str,
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_db),
):
    from src.integrations.control_plane import IntegrationControlPlane

    cp = IntegrationControlPlane(db, workspace_id)
    inst = await cp.get_installation(install_id)
    if not inst:
        raise HTTPException(status_code=404, detail="Installation not found")
    return InstallationResponse(
        install_id=inst.install_id,
        server_name=inst.server_name,
        display_name=inst.display_name,
        transport=inst.transport,
        status=inst.status,
        health_status=inst.health_status,
        enabled=inst.enabled,
        auth_provider=inst.auth_provider,
        scopes_granted=inst.scopes_granted,
        created_at=inst.created_at.isoformat() if inst.created_at else None,
    )


async def _clear_connection_artifacts(
    db: AsyncSession,
    inst,
    user_id: str,
    workspace_id: str,
) -> None:
    """Revoke credentials + close live sessions for an integration.

    Shared by /disconnect (keeps row) and DELETE (drops row).
    Revokes the credential — the OAuth token for a native installation, the
    `connection_map` rows for a gateway-backed one — then closes cached MCP
    sessions and unregisters the server config so stale tool schemas are not
    reused on reconnect.
    """
    from sqlalchemy import update

    from src.config.settings import get_settings
    from src.integrations.gateway_actions import providers_for_server
    from src.integrations.mcp_pool import get_workspace_pool
    from src.integrations.provider_map import (
        native_perception_for_provider,
        provider_for_server,
    )
    from src.models.connection_map import ConnectionMap
    from src.models.database import get_session_factory
    from src.services.oauth_manager import OAuthManager

    settings = get_settings()

    # Gateway-backed installations hold no OAuthManager token: their credential
    # lives in OpenConnector and `integration_status` reads connectivity from
    # `connection_map`. They also all declare auth_provider="platform_jwt",
    # which misses the OAuth provider map below — so without this branch
    # Disconnect would clear sessions, then the next status refetch would read
    # the still-"active" rows and report connected again.
    gateway_providers = providers_for_server(inst.server_name)
    if gateway_providers:
        # Scoped exactly like the rest of this endpoint: this workspace, this
        # principal. Every alias of the installation's providers is revoked —
        # "Disconnect" means all of this user's accounts for this integration.
        # TODO: revoking on the OpenConnector side (deleting the stored
        # credential via the OC admin API) is still outstanding — a later wave.
        # Flipping the local rows only stops Muldro resolving/reporting them.
        await db.execute(
            update(ConnectionMap)
            .where(
                ConnectionMap.workspace_id == workspace_id,
                ConnectionMap.principal_id == user_id,
                ConnectionMap.provider_id.in_(gateway_providers),
            )
            .values(connection_status="revoked")
        )

    # Map server auth_provider → OAuth provider name (same table used by
    # /unified). Only oauth-backed installations have tokens to delete.
    provider_map = {
        "google": "google",
        "github": "github",
        "slack": "slack",
        "atlassian": "atlassian",
    }
    provider_name = provider_map.get(inst.auth_provider or "")

    # A gateway installation can ALSO hold its own native OAuth token, for a
    # poll the OpenConnector catalog has no action to serve. Every such
    # installation declares auth_provider="platform_jwt", which the map above
    # cannot answer for — so this used to revoke the gateway rows, report
    # success, and leave the native grant standing. The card then read "Not
    # connected" while Muldro still held a credential it kept polling with.
    # Disconnect ends every grant the installation holds, or it is not a
    # revocation control.
    #
    # Asked of the REGISTRY, and specifically of `native_perception_for_provider`
    # — never `sources_for_provider`, whose identity fallback answers "yes" for
    # every provider ever named and would try to delete a token for the
    # gateway-only ones too.
    if not provider_name and gateway_providers:
        brand = provider_for_server(inst.server_name)
        if native_perception_for_provider(brand) is not None:
            provider_name = brand

    if provider_name and settings.oauth_encryption_key:
        db_factory = get_session_factory()
        oauth_mgr = OAuthManager(
            db_factory,
            encryption_key=settings.oauth_encryption_key,
            settings=settings,
        )
        try:
            await oauth_mgr.delete_token(user_id, provider_name)
        except Exception:
            logger.warning(
                "Failed to delete OAuth token during disconnect (provider=%s user=%s)",
                provider_name,
                user_id,
                exc_info=True,
            )

    # Close live MCP sessions + unregister server config/tool schemas.
    pool = get_workspace_pool()
    if pool:
        try:
            await pool.remove_server(workspace_id, inst.server_name)
        except Exception:
            logger.warning(
                "Failed to remove MCP pool entry during disconnect (server=%s workspace=%s)",
                inst.server_name,
                workspace_id,
                exc_info=True,
            )


@router.post("/{install_id}/disconnect", response_model=UnifiedIntegrationResponse)
async def disconnect_installation(
    install_id: str,
    user_id: str = Depends(get_current_user_id),
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_db),
):
    """Clear credentials and close sessions, keeping the installation row.

    This is the primary action behind the "Disconnect" button. The catalog
    row stays so the integration remains listed as "Not connected" and can
    be reconnected via a fresh OAuth flow. Use DELETE /{id} to also drop
    the row (rarely needed — seed sync restores default installations on
    restart).
    """
    from src.integrations.control_plane import IntegrationControlPlane

    cp = IntegrationControlPlane(db, workspace_id)
    inst = await cp.get_installation(install_id)
    if not inst:
        raise HTTPException(status_code=404, detail="Installation not found")

    await _clear_connection_artifacts(db, inst, user_id, workspace_id)
    await db.commit()

    # Compute category the same way list_unified_integrations does,
    # so the frontend stays consistent after optimistic updates refetch.
    if inst.auth_provider is None:
        category = "local"
    elif inst.auth_provider == "token":
        category = "token"
    else:
        category = "oauth"

    provider_name: str | None = None
    if inst.auth_provider and inst.auth_provider not in ("token", "none"):
        provider_name = inst.auth_provider

    from src.integrations.gateway_actions import provider_labels_for_server, providers_for_server
    from src.integrations.provider_map import native_perception_for_provider, provider_for_server
    from src.services.integration_status import coarsen_scopes, derive_slug

    # Every gateway installation declares the same auth_provider
    # ("platform_jwt"), so deriving the brand slug from it collapses them
    # all into "platform" — a collision between google-workspace and github.
    # Derive from server_name for those instead, matching integration_status.py.
    gateway_providers = providers_for_server(inst.server_name)
    is_gateway = bool(gateway_providers)
    native_name = provider_for_server(inst.server_name) if is_gateway else ""
    native = native_perception_for_provider(native_name) if native_name else None

    raw_scopes = inst.scopes_granted or []
    return UnifiedIntegrationResponse(
        server_name=inst.server_name,
        display_name=inst.display_name,
        provider=provider_name,
        category=category,
        configured=True,
        connected=False,
        health_status=inst.health_status,
        enabled=inst.enabled,
        install_id=inst.install_id,
        scopes=raw_scopes,
        slug=derive_slug(None if is_gateway else provider_name, inst.server_name),
        access_scopes=coarsen_scopes(raw_scopes),
        # Every provider this installation serves is disconnected post-clear,
        # so all-False here — same shape list_unified_integrations returns for
        # a fully disconnected gateway installation.
        oc_providers=list(gateway_providers),
        provider_connections={p: False for p in gateway_providers},
        oc_provider_labels=provider_labels_for_server(inst.server_name),
        # Named so the card keeps its second chip through the optimistic update;
        # the refetch this response triggers re-reads the real token state.
        native_provider=native_name if native else None,
        native_purpose=native.purpose if native else "",
        native_connected=False,
    )


@router.delete("/{install_id}", status_code=204)
async def delete_installation(
    install_id: str,
    user_id: str = Depends(get_current_user_id),
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_db),
):
    from src.integrations.control_plane import IntegrationControlPlane

    cp = IntegrationControlPlane(db, workspace_id)
    inst = await cp.get_installation(install_id)
    if not inst:
        raise HTTPException(status_code=404, detail="Installation not found")

    # Revoke credentials + close sessions before dropping the row,
    # otherwise tokens and live sessions leak past the delete.
    await _clear_connection_artifacts(db, inst, user_id, workspace_id)

    await cp.delete_installation(install_id)
    await db.commit()


@router.post("/{install_id}/pause", response_model=HealthCheckResponse)
async def pause_installation(
    install_id: str,
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_db),
):
    from src.integrations.control_plane import IntegrationControlPlane

    cp = IntegrationControlPlane(db, workspace_id)
    ok = await cp.pause_installation(install_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Installation not found")
    await db.commit()
    return HealthCheckResponse(install_id=install_id, health_status="unavailable")


@router.post("/{install_id}/resume", response_model=HealthCheckResponse)
async def resume_installation(
    install_id: str,
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_db),
):
    from src.integrations.control_plane import IntegrationControlPlane

    cp = IntegrationControlPlane(db, workspace_id)
    ok = await cp.resume_installation(install_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Installation not found")
    await db.commit()
    health = await cp.check_health(install_id)
    await db.commit()
    return HealthCheckResponse(install_id=install_id, health_status=health)


@router.get("/{install_id}/health", response_model=HealthCheckResponse)
async def check_installation_health(
    install_id: str,
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_db),
):
    from src.integrations.control_plane import IntegrationControlPlane

    cp = IntegrationControlPlane(db, workspace_id)
    inst = await cp.get_installation(install_id)
    if not inst:
        raise HTTPException(status_code=404, detail="Installation not found")
    health = await cp.check_health(install_id)
    await db.commit()
    return HealthCheckResponse(
        install_id=install_id,
        server_name=inst.server_name,
        health_status=health,
    )


@router.get("/health/all", response_model=list[HealthCheckResponse])
async def check_all_health(
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_db),
):
    from src.integrations.control_plane import IntegrationControlPlane

    cp = IntegrationControlPlane(db, workspace_id)
    results = await cp.check_all_health()
    await db.commit()
    return [
        HealthCheckResponse(server_name=name, health_status=status)
        for name, status in results.items()
    ]


@router.get("/trust/records", response_model=list[TrustRecordResponse])
async def list_trust_records(
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_db),
):
    from sqlalchemy import select as sa_select

    from src.models.server_trust import ServerTrustRecord

    result = await db.execute(
        sa_select(ServerTrustRecord)
        .where(ServerTrustRecord.workspace_id == workspace_id)
        .order_by(ServerTrustRecord.trust_tier, ServerTrustRecord.server_name)
    )
    records = result.scalars().all()
    return [
        TrustRecordResponse(
            trust_id=r.trust_id,
            server_name=r.server_name,
            trust_tier=r.trust_tier,
            verified_by=r.verified_by,
            status=r.status,
            created_at=r.created_at.isoformat() if r.created_at else None,
        )
        for r in records
    ]


@router.post("/circuit-breaker/{server_name}/reset")
async def reset_circuit_breaker(
    server_name: str,
    user_id: str = Depends(get_current_user_id),
):
    """Reset circuit breaker for an MCP server (admin action)."""
    from src.connectors.mcp_bridge import _circuit_breaker

    _circuit_breaker.reset(server_name)
    return {"status": "reset", "server": server_name}
