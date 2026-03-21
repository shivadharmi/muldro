"""API routes for user MCP management, catalog, and audit trail.

Endpoints:
  POST   /v1/mcp/register           — register a new MCP server
  POST   /v1/mcp/{catalog_id}/inspect   — inspect server manifest
  POST   /v1/mcp/{catalog_id}/activate  — activate server
  POST   /v1/mcp/{catalog_id}/revoke    — revoke server
  GET    /v1/mcp/catalog             — list catalog entries
  GET    /v1/mcp/catalog/{catalog_id} — get catalog entry
  DELETE /v1/mcp/catalog/{catalog_id} — deprecate catalog entry
  GET    /v1/mcp/allowlist           — list allowlist
  POST   /v1/mcp/allowlist           — add to allowlist
  DELETE /v1/mcp/allowlist/{id}      — remove from allowlist
  GET    /v1/mcp/audit               — list audit trail
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_current_user_id, get_current_workspace_id
from src.models.database import get_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/mcp")


# ── Request/Response schemas ─────────────────────────────────────────


class RegisterRequest(BaseModel):
    server_name: str
    display_name: str
    description: str | None = None
    transport: str = "stdio"
    command: str | None = None
    args_template: dict | None = None
    env_template: dict | None = None
    remote_url: str | None = None
    publisher: str | None = None
    source_url: str | None = None
    tags: list[str] | None = None


class InspectRequest(BaseModel):
    tools: list[dict]


class OnboardingResponse(BaseModel):
    status: str
    catalog_id: str | None = None
    install_id: str | None = None
    trust_id: str | None = None
    block_reason: str | None = None
    error: str | None = None
    inspection_summary: str | None = None
    risk_score: int | None = None
    recommended_tier: str | None = None


class CatalogResponse(BaseModel):
    catalog_id: str
    server_name: str
    display_name: str
    description: str | None = None
    publisher: str | None = None
    risk_score: int
    default_trust_tier: str
    verified: bool
    tool_count: int
    status: str
    tags: list[str]


class AllowlistEntryResponse(BaseModel):
    allowlist_id: str
    server_name: str
    max_trust_tier: str
    requires_approval: bool
    enabled: bool
    reason: str | None = None


class AddAllowlistRequest(BaseModel):
    server_name: str
    max_trust_tier: str = "T2"
    requires_approval: bool = True
    server_url_pattern: str | None = None
    reason: str | None = None


class AuditEventResponse(BaseModel):
    audit_id: str
    server_name: str
    tool_name: str
    trust_tier: str
    action: str
    status: str
    error_message: str | None = None
    latency_ms: int | None = None
    occurred_at: str | None = None


# ── User MCP endpoints ──────────────────────────────────────────────


@router.post("/register", response_model=OnboardingResponse, status_code=201)
async def register_mcp_server(
    body: RegisterRequest,
    user_id: str = Depends(get_current_user_id),
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_db),
):
    from src.integrations.onboarding import MCPOnboardingService

    svc = MCPOnboardingService(db, workspace_id)
    result = await svc.register(
        user_id=user_id,
        server_name=body.server_name,
        display_name=body.display_name,
        description=body.description,
        transport=body.transport,
        command=body.command,
        args_template=body.args_template,
        env_template=body.env_template,
        remote_url=body.remote_url,
        publisher=body.publisher,
        source_url=body.source_url,
        tags=body.tags,
    )
    if result.error:
        raise HTTPException(status_code=409, detail=result.error)
    await db.commit()
    return OnboardingResponse(
        status=result.status,
        catalog_id=result.catalog_id,
    )


@router.post("/{catalog_id}/inspect", response_model=OnboardingResponse)
async def inspect_mcp_server(
    catalog_id: str,
    body: InspectRequest,
    user_id: str = Depends(get_current_user_id),
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_db),
):
    from src.integrations.onboarding import MCPOnboardingService

    svc = MCPOnboardingService(db, workspace_id)
    result = await svc.inspect(catalog_id, body.tools)
    if result.error:
        raise HTTPException(status_code=404, detail=result.error)
    await db.commit()
    return OnboardingResponse(
        status=result.status,
        catalog_id=result.catalog_id,
        inspection_summary=result.inspection.summary if result.inspection else None,
        risk_score=result.inspection.risk_score if result.inspection else None,
        recommended_tier=result.inspection.recommended_tier if result.inspection else None,
    )


@router.post("/{catalog_id}/activate", response_model=OnboardingResponse)
async def activate_mcp_server(
    catalog_id: str,
    user_id: str = Depends(get_current_user_id),
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_db),
):
    from src.integrations.onboarding import MCPOnboardingService

    svc = MCPOnboardingService(db, workspace_id)

    # Check org allowlist first
    check_result = await svc.classify_and_check(catalog_id)
    if check_result.status == "blocked":
        return OnboardingResponse(
            status="blocked",
            catalog_id=catalog_id,
            block_reason=check_result.block_reason,
        )

    result = await svc.activate(catalog_id, user_id)
    if result.error:
        raise HTTPException(status_code=400, detail=result.error)
    await db.commit()
    return OnboardingResponse(
        status=result.status,
        catalog_id=result.catalog_id,
        install_id=result.install_id,
        trust_id=result.trust_id,
    )


@router.post("/{catalog_id}/revoke", response_model=OnboardingResponse)
async def revoke_mcp_server(
    catalog_id: str,
    user_id: str = Depends(get_current_user_id),
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_db),
):
    from src.integrations.onboarding import MCPOnboardingService

    svc = MCPOnboardingService(db, workspace_id)
    result = await svc.revoke(catalog_id)
    if result.error:
        raise HTTPException(status_code=404, detail=result.error)
    await db.commit()
    return OnboardingResponse(status="revoked", catalog_id=catalog_id)


# ── Catalog endpoints ────────────────────────────────────────────────


@router.get("/catalog", response_model=list[CatalogResponse])
async def list_catalog(
    verified_only: bool = False,
    tag: str | None = None,
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_db),
):
    from src.integrations.org_controls import OrgControlService

    svc = OrgControlService(db, workspace_id)
    entries = await svc.list_catalog(verified_only=verified_only, tag=tag)
    return [
        CatalogResponse(
            catalog_id=e.catalog_id,
            server_name=e.server_name,
            display_name=e.display_name,
            description=e.description,
            publisher=e.publisher,
            risk_score=e.risk_score,
            default_trust_tier=e.default_trust_tier,
            verified=e.verified,
            tool_count=e.tool_count,
            status=e.status,
            tags=e.tags,
        )
        for e in entries
    ]


@router.get("/catalog/{catalog_id}", response_model=CatalogResponse)
async def get_catalog_entry(
    catalog_id: str,
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_db),
):
    from src.integrations.org_controls import OrgControlService

    svc = OrgControlService(db, workspace_id)
    entry = await svc.get_catalog_entry(catalog_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Catalog entry not found")
    return CatalogResponse(
        catalog_id=entry.catalog_id,
        server_name=entry.server_name,
        display_name=entry.display_name,
        description=entry.description,
        publisher=entry.publisher,
        risk_score=entry.risk_score,
        default_trust_tier=entry.default_trust_tier,
        verified=entry.verified,
        tool_count=entry.tool_count,
        status=entry.status,
        tags=entry.tags,
    )


@router.delete("/catalog/{catalog_id}", status_code=204)
async def deprecate_catalog_entry(
    catalog_id: str,
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_db),
):
    from src.integrations.org_controls import OrgControlService

    svc = OrgControlService(db, workspace_id)
    ok = await svc.deprecate_catalog_entry(catalog_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Catalog entry not found")
    await db.commit()


# ── Allowlist endpoints ──────────────────────────────────────────────


@router.get("/allowlist", response_model=list[AllowlistEntryResponse])
async def list_allowlist(
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_db),
):
    from src.integrations.org_controls import OrgControlService

    svc = OrgControlService(db, workspace_id)
    entries = await svc.list_allowlist()
    return [
        AllowlistEntryResponse(
            allowlist_id=e.allowlist_id,
            server_name=e.server_name,
            max_trust_tier=e.max_trust_tier,
            requires_approval=e.requires_approval,
            enabled=e.enabled,
            reason=e.reason,
        )
        for e in entries
    ]


@router.post("/allowlist", response_model=AllowlistEntryResponse, status_code=201)
async def add_to_allowlist(
    body: AddAllowlistRequest,
    user_id: str = Depends(get_current_user_id),
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_db),
):
    from src.integrations.org_controls import OrgControlService

    svc = OrgControlService(db, workspace_id)
    entry = await svc.add_to_allowlist(
        server_name=body.server_name,
        added_by=user_id,
        max_trust_tier=body.max_trust_tier,
        requires_approval=body.requires_approval,
        server_url_pattern=body.server_url_pattern,
        reason=body.reason,
    )
    await db.commit()
    return AllowlistEntryResponse(
        allowlist_id=entry.allowlist_id,
        server_name=entry.server_name,
        max_trust_tier=entry.max_trust_tier,
        requires_approval=entry.requires_approval,
        enabled=entry.enabled,
        reason=entry.reason,
    )


@router.delete("/allowlist/{allowlist_id}", status_code=204)
async def remove_from_allowlist(
    allowlist_id: str,
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_db),
):
    from src.integrations.org_controls import OrgControlService

    svc = OrgControlService(db, workspace_id)
    ok = await svc.remove_from_allowlist(allowlist_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Allowlist entry not found")
    await db.commit()


# ── Audit trail endpoint ────────────────────────────────────────────


@router.get("/audit", response_model=list[AuditEventResponse])
async def list_audit_events(
    server_name: str | None = None,
    action: str | None = None,
    status: str | None = None,
    limit: int = Query(default=50, le=200),
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_db),
):
    from src.models.integration_audit import IntegrationAuditEvent

    stmt = select(IntegrationAuditEvent).where(IntegrationAuditEvent.workspace_id == workspace_id)
    if server_name:
        stmt = stmt.where(IntegrationAuditEvent.server_name == server_name)
    if action:
        stmt = stmt.where(IntegrationAuditEvent.action == action)
    if status:
        stmt = stmt.where(IntegrationAuditEvent.status == status)
    stmt = stmt.order_by(IntegrationAuditEvent.occurred_at.desc()).limit(limit)

    result = await db.execute(stmt)
    events = result.scalars().all()
    return [
        AuditEventResponse(
            audit_id=e.audit_id,
            server_name=e.server_name,
            tool_name=e.tool_name,
            trust_tier=e.trust_tier,
            action=e.action,
            status=e.status,
            error_message=e.error_message,
            latency_ms=e.latency_ms,
            occurred_at=e.occurred_at.isoformat() if e.occurred_at else None,
        )
        for e in events
    ]
