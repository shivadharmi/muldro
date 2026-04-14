"""API routes for the integration platform.

Endpoints:
  GET    /v1/integrations                — list installations
  GET    /v1/integrations/unified        — unified view for frontend
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
from pydantic import BaseModel
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
    category: str  # "oauth" | "token" | "local"
    provider: str | None = None
    configured: bool
    connected: bool
    health_status: str
    scopes: list[str]
    install_id: str | None = None


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
    """Unified integration view merging installations with OAuth connection status.

    Returns one entry per installation with:
    - category: "oauth" | "token" | "local" (derived from auth_provider)
    - provider: OAuth provider name for Connect button (maps atlassian -> jira)
    - configured: whether OAuth credentials are set
    - connected: whether a valid token exists
    - health_status, scopes, install_id
    """
    from src.config.settings import get_settings
    from src.integrations.control_plane import IntegrationControlPlane

    settings = get_settings()
    cp = IntegrationControlPlane(db, workspace_id)
    installations = await cp.list_installations()

    # Check OAuth connection status
    connected_providers: set[str] = set()
    try:
        from src.models.database import get_session_factory
        from src.services.oauth_manager import OAuthManager

        db_factory = get_session_factory()
        oauth_mgr = OAuthManager(db_factory, user_id, settings=settings)
        for provider_name in ("google", "github", "slack", "linear", "notion", "jira"):
            try:
                token = await oauth_mgr.get_valid_token(user_id, provider_name)
                if token:
                    connected_providers.add(provider_name)
            except Exception:
                pass
    except Exception:
        pass

    # Map auth_provider to category and provider name
    auth_to_category = {
        "google": "oauth",
        "github": "oauth",
        "slack": "token",
        "linear": "oauth",
        "notion": "oauth",
        "oauth": "oauth",  # atlassian uses "oauth"
        "token": "token",
    }

    # Map server_name to the OAuth provider for the Connect button
    server_to_provider = {
        "google-workspace": "google",
        "github": "github",
        "slack": "slack",
        "linear": "linear",
        "notion": "notion",
        "atlassian": "jira",
    }

    results = []
    for inst in installations:
        auth = inst.auth_provider
        category = auth_to_category.get(auth, "local") if auth else "local"
        provider = server_to_provider.get(inst.server_name)

        # Determine if OAuth is configured
        configured = True
        if provider:
            oauth_name = provider if provider != "jira" else "jira"
            client_id = getattr(settings, f"{oauth_name}_oauth_client_id", "")
            configured = bool(client_id)

        # Check if connected via OAuth
        connected = False
        if provider:
            oauth_check = provider if provider != "jira" else "jira"
            connected = oauth_check in connected_providers
        elif category == "token":
            # Token-based: check if env vars are set (via env_template)
            env_template = inst.env_template or {}
            connected = all(
                bool(import_os_environ_get(k)) for k in env_template
            ) if env_template else False
        elif category == "local":
            connected = inst.status == "active"

        results.append(
            UnifiedIntegrationResponse(
                server_name=inst.server_name,
                display_name=inst.display_name,
                category=category,
                provider=provider,
                configured=configured,
                connected=connected,
                health_status=inst.health_status,
                scopes=inst.scopes_granted or [],
                install_id=inst.install_id,
            )
        )

    return results


def import_os_environ_get(key: str) -> str:
    """Get an environment variable value."""
    import os

    return os.environ.get(key, "")


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


@router.delete("/{install_id}", status_code=204)
async def delete_installation(
    install_id: str,
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_db),
):
    from src.integrations.control_plane import IntegrationControlPlane

    cp = IntegrationControlPlane(db, workspace_id)
    deleted = await cp.delete_installation(install_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Installation not found")
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
