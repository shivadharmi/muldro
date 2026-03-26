"""Org controls — allowlists and private catalog management.

Provides admin-level controls for managing which MCP servers are allowed
in a workspace, catalog visibility, and default tier assignments.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.mcp_server_catalog import MCPServerCatalog
from src.models.org_allowlist import OrgAllowlist

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AllowlistEntry:
    allowlist_id: str
    server_name: str
    max_trust_tier: str
    requires_approval: bool
    enabled: bool
    reason: str | None


@dataclass(frozen=True)
class CatalogEntry:
    catalog_id: str
    server_name: str
    display_name: str
    description: str | None
    publisher: str | None
    risk_score: int
    default_trust_tier: str
    verified: bool
    tool_count: int
    status: str
    tags: list[str]


class OrgControlService:
    def __init__(self, db: AsyncSession, workspace_id: str) -> None:
        self._db = db
        self._workspace_id = workspace_id

    # ── Allowlist management ─────────────────────────────────────────

    async def list_allowlist(self) -> list[AllowlistEntry]:
        result = await self._db.execute(
            select(OrgAllowlist)
            .where(OrgAllowlist.workspace_id == self._workspace_id)
            .order_by(OrgAllowlist.server_name)
        )
        return [
            AllowlistEntry(
                allowlist_id=e.allowlist_id,
                server_name=e.server_name,
                max_trust_tier=e.max_trust_tier,
                requires_approval=e.requires_approval,
                enabled=e.enabled,
                reason=e.reason,
            )
            for e in result.scalars().all()
        ]

    async def add_to_allowlist(
        self,
        server_name: str,
        added_by: str,
        max_trust_tier: str = "T2",
        requires_approval: bool = True,
        server_url_pattern: str | None = None,
        allowed_capabilities: dict | None = None,
        blocked_capabilities: dict | None = None,
        reason: str | None = None,
    ) -> AllowlistEntry:
        entry = OrgAllowlist(
            workspace_id=self._workspace_id,
            server_name=server_name,
            server_url_pattern=server_url_pattern,
            max_trust_tier=max_trust_tier,
            allowed_capabilities=allowed_capabilities,
            blocked_capabilities=blocked_capabilities,
            requires_approval=requires_approval,
            added_by=added_by,
            reason=reason,
        )
        self._db.add(entry)
        await self._db.flush()
        return AllowlistEntry(
            allowlist_id=entry.allowlist_id,
            server_name=entry.server_name,
            max_trust_tier=entry.max_trust_tier,
            requires_approval=entry.requires_approval,
            enabled=entry.enabled,
            reason=entry.reason,
        )

    async def remove_from_allowlist(self, allowlist_id: str) -> bool:
        result = await self._db.execute(
            select(OrgAllowlist).where(
                OrgAllowlist.allowlist_id == allowlist_id,
                OrgAllowlist.workspace_id == self._workspace_id,
            )
        )
        entry = result.scalar_one_or_none()
        if not entry:
            return False
        entry.enabled = False
        return True

    async def is_allowed(self, server_name: str) -> bool:
        """Check if a server is on the allowlist (or if there's no allowlist)."""
        has_entries = await self._db.scalar(
            select(OrgAllowlist.allowlist_id)
            .where(
                OrgAllowlist.workspace_id == self._workspace_id,
                OrgAllowlist.enabled.is_(True),
            )
            .limit(1)
        )
        if not has_entries:
            return True  # no allowlist = everything allowed

        result = await self._db.execute(
            select(OrgAllowlist).where(
                OrgAllowlist.workspace_id == self._workspace_id,
                OrgAllowlist.server_name == server_name,
                OrgAllowlist.enabled.is_(True),
            )
        )
        return result.scalar_one_or_none() is not None

    # ── Catalog management ───────────────────────────────────────────

    async def list_catalog(
        self,
        verified_only: bool = False,
        tag: str | None = None,
    ) -> list[CatalogEntry]:
        stmt = select(MCPServerCatalog).where(
            MCPServerCatalog.workspace_id == self._workspace_id,
            MCPServerCatalog.status != "removed",
        )
        if verified_only:
            stmt = stmt.where(MCPServerCatalog.verified.is_(True))
        stmt = stmt.order_by(MCPServerCatalog.server_name)

        result = await self._db.execute(stmt)
        entries = result.scalars().all()

        catalog_list = []
        for e in entries:
            if tag and (not e.tags or tag not in e.tags):
                continue
            catalog_list.append(
                CatalogEntry(
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
                    tags=e.tags or [],
                )
            )
        return catalog_list

    async def get_catalog_entry(self, catalog_id: str) -> CatalogEntry | None:
        result = await self._db.execute(
            select(MCPServerCatalog).where(
                MCPServerCatalog.catalog_id == catalog_id,
                MCPServerCatalog.workspace_id == self._workspace_id,
            )
        )
        e = result.scalar_one_or_none()
        if not e:
            return None
        return CatalogEntry(
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
            tags=e.tags or [],
        )

    async def deprecate_catalog_entry(self, catalog_id: str) -> bool:
        result = await self._db.execute(
            select(MCPServerCatalog).where(
                MCPServerCatalog.catalog_id == catalog_id,
                MCPServerCatalog.workspace_id == self._workspace_id,
            )
        )
        entry = result.scalar_one_or_none()
        if not entry:
            return False
        entry.status = "deprecated"
        return True
