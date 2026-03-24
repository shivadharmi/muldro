"""MCP server onboarding — register, inspect, classify, activate.

Orchestrates the full lifecycle of adding a user MCP server:
1. Register: create catalog entry
2. Inspect: fetch manifest, classify tools, compute risk score
3. Classify: determine trust tier, check org allowlist
4. Activate: create installation + trust record + capability bindings
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.integrations.manifest_inspector import InspectionResult, inspect_manifest
from src.models.capability_binding import CapabilityBinding
from src.models.integration_installation import IntegrationInstallation
from src.models.mcp_server_catalog import MCPServerCatalog
from src.models.org_allowlist import OrgAllowlist
from src.models.server_trust import ServerTrustRecord

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OnboardingResult:
    status: Literal["registered", "inspected", "activated", "revoked", "blocked", "failed"]
    catalog_id: str | None = None
    install_id: str | None = None
    trust_id: str | None = None
    inspection: InspectionResult | None = None
    block_reason: str | None = None
    error: str | None = None


class MCPOnboardingService:
    def __init__(self, db: AsyncSession, workspace_id: str) -> None:
        self._db = db
        self._workspace_id = workspace_id

    async def register(
        self,
        user_id: str,
        server_name: str,
        display_name: str,
        description: str | None = None,
        transport: str = "stdio",
        command: str | None = None,
        args_template: dict | None = None,
        env_template: dict | None = None,
        remote_url: str | None = None,
        publisher: str | None = None,
        source_url: str | None = None,
        tags: list[str] | None = None,
    ) -> OnboardingResult:
        """Step 1: Register server in the catalog."""
        # Check for duplicate
        existing = await self._db.execute(
            select(MCPServerCatalog).where(
                MCPServerCatalog.workspace_id == self._workspace_id,
                MCPServerCatalog.server_name == server_name,
            )
        )
        if existing.scalar_one_or_none():
            return OnboardingResult(
                status="failed",
                error=f"Server '{server_name}' already exists in catalog",
            )

        catalog_entry = MCPServerCatalog(
            workspace_id=self._workspace_id,
            server_name=server_name,
            display_name=display_name,
            description=description,
            transport=transport,
            command=command,
            args_template=args_template,
            env_template=env_template,
            remote_url=remote_url,
            publisher=publisher,
            source_url=source_url,
            tags=tags,
            status="pending",
        )
        self._db.add(catalog_entry)
        await self._db.flush()

        return OnboardingResult(
            status="registered",
            catalog_id=catalog_entry.catalog_id,
        )

    async def inspect(
        self,
        catalog_id: str,
        tools: list[dict],
    ) -> OnboardingResult:
        """Step 2: Inspect the manifest and update catalog with risk assessment."""
        result = await self._db.execute(
            select(MCPServerCatalog).where(
                MCPServerCatalog.catalog_id == catalog_id,
                MCPServerCatalog.workspace_id == self._workspace_id,
            )
        )
        catalog_entry = result.scalar_one_or_none()
        if not catalog_entry:
            return OnboardingResult(status="failed", error="Catalog entry not found")

        inspection = inspect_manifest(catalog_entry.server_name, tools)

        catalog_entry.manifest_hash = inspection.manifest_hash
        catalog_entry.tool_count = inspection.tool_count
        catalog_entry.risk_score = inspection.risk_score
        catalog_entry.risk_factors = {
            "factors": inspection.risk_factors,
            "has_write": inspection.has_write_tools,
            "has_sensitive": inspection.has_sensitive_access,
        }
        catalog_entry.capabilities = inspection.capabilities
        catalog_entry.default_trust_tier = inspection.recommended_tier
        catalog_entry.status = "inspected"

        return OnboardingResult(
            status="inspected",
            catalog_id=catalog_id,
            inspection=inspection,
        )

    async def classify_and_check(self, catalog_id: str) -> OnboardingResult:
        """Step 3: Check org allowlist and classify trust tier."""
        result = await self._db.execute(
            select(MCPServerCatalog).where(
                MCPServerCatalog.catalog_id == catalog_id,
                MCPServerCatalog.workspace_id == self._workspace_id,
            )
        )
        catalog_entry = result.scalar_one_or_none()
        if not catalog_entry:
            return OnboardingResult(status="failed", error="Catalog entry not found")

        # Check org allowlist
        allowlist_result = await self._db.execute(
            select(OrgAllowlist).where(
                OrgAllowlist.workspace_id == self._workspace_id,
                OrgAllowlist.server_name == catalog_entry.server_name,
                OrgAllowlist.enabled.is_(True),
            )
        )
        allowlist_entry = allowlist_result.scalar_one_or_none()

        # If org has an allowlist, check entries exist
        has_allowlist = await self._db.scalar(
            select(OrgAllowlist.allowlist_id)
            .where(
                OrgAllowlist.workspace_id == self._workspace_id,
                OrgAllowlist.enabled.is_(True),
            )
            .limit(1)
        )

        if has_allowlist and not allowlist_entry:
            return OnboardingResult(
                status="blocked",
                catalog_id=catalog_id,
                block_reason=(f"Server '{catalog_entry.server_name}' is not on the org allowlist"),
            )

        if allowlist_entry and allowlist_entry.requires_approval:
            return OnboardingResult(
                status="blocked",
                catalog_id=catalog_id,
                block_reason="Admin approval required for this server",
            )

        return OnboardingResult(status="inspected", catalog_id=catalog_id)

    async def activate(
        self,
        catalog_id: str,
        user_id: str,
    ) -> OnboardingResult:
        """Step 4: Create installation, trust record, and capability bindings."""
        result = await self._db.execute(
            select(MCPServerCatalog).where(
                MCPServerCatalog.catalog_id == catalog_id,
                MCPServerCatalog.workspace_id == self._workspace_id,
            )
        )
        catalog_entry = result.scalar_one_or_none()
        if not catalog_entry:
            return OnboardingResult(status="failed", error="Catalog entry not found")

        if catalog_entry.status not in ("inspected", "active"):
            return OnboardingResult(
                status="failed",
                error=f"Cannot activate server in '{catalog_entry.status}' state",
            )

        # Create trust record
        trust_record = ServerTrustRecord(
            workspace_id=self._workspace_id,
            server_name=catalog_entry.server_name,
            server_url=catalog_entry.remote_url,
            trust_tier=catalog_entry.default_trust_tier,
            verified_by=f"onboarding:{user_id}",
            manifest_hash=catalog_entry.manifest_hash,
            status="active",
        )
        self._db.add(trust_record)
        await self._db.flush()

        # Create installation
        installation = IntegrationInstallation(
            workspace_id=self._workspace_id,
            user_id=user_id,
            server_name=catalog_entry.server_name,
            display_name=catalog_entry.display_name,
            transport=catalog_entry.transport,
            command=catalog_entry.command,
            args=catalog_entry.args_template,
            env_template=catalog_entry.env_template,
            remote_url=catalog_entry.remote_url,
            trust_id=trust_record.trust_id,
            status="active",
            health_status="unknown",
            config={"catalog_id": catalog_id},
        )
        self._db.add(installation)
        await self._db.flush()

        # Clean up orphaned capability bindings from previous activations
        old_bindings = await self._db.execute(
            select(CapabilityBinding).where(
                CapabilityBinding.workspace_id == self._workspace_id,
                CapabilityBinding.backend_ref == catalog_entry.server_name,
            )
        )
        for old in old_bindings.scalars().all():
            await self._db.delete(old)

        # Create capability bindings for discovered capabilities
        for cap in catalog_entry.capabilities or []:
            binding = CapabilityBinding(
                workspace_id=self._workspace_id,
                capability=cap,
                family=cap.split(".")[0] if "." in cap else "unknown",
                backend_type="mcp_user",
                backend_ref=catalog_entry.server_name,
                tool_name=cap,
                priority=30,  # user MCP gets lowest priority
                enabled=True,
                trust_id=trust_record.trust_id,
            )
            self._db.add(binding)

        catalog_entry.status = "active"
        catalog_entry.verified = False

        # Register in workspace pool for immediate availability (no restart)
        from src.integrations.mcp_pool import _installation_to_config, get_workspace_pool

        pool = get_workspace_pool()
        if pool:
            config = _installation_to_config(installation)
            await pool.add_server(
                self._workspace_id, catalog_entry.server_name, config,
            )

        return OnboardingResult(
            status="activated",
            catalog_id=catalog_id,
            install_id=installation.install_id,
            trust_id=trust_record.trust_id,
        )

    async def revoke(self, catalog_id: str) -> OnboardingResult:
        """Revoke an activated server — deactivate installation and trust."""
        result = await self._db.execute(
            select(MCPServerCatalog).where(
                MCPServerCatalog.catalog_id == catalog_id,
                MCPServerCatalog.workspace_id == self._workspace_id,
            )
        )
        catalog_entry = result.scalar_one_or_none()
        if not catalog_entry:
            return OnboardingResult(status="failed", error="Catalog entry not found")

        # Deactivate installations
        inst_result = await self._db.execute(
            select(IntegrationInstallation).where(
                IntegrationInstallation.workspace_id == self._workspace_id,
                IntegrationInstallation.server_name == catalog_entry.server_name,
            )
        )
        for inst in inst_result.scalars().all():
            inst.status = "disabled"
            inst.enabled = False

        # Deactivate trust records
        trust_result = await self._db.execute(
            select(ServerTrustRecord).where(
                ServerTrustRecord.workspace_id == self._workspace_id,
                ServerTrustRecord.server_name == catalog_entry.server_name,
            )
        )
        for trust in trust_result.scalars().all():
            trust.status = "revoked"

        # Disable capability bindings
        binding_result = await self._db.execute(
            select(CapabilityBinding).where(
                CapabilityBinding.workspace_id == self._workspace_id,
                CapabilityBinding.backend_ref == catalog_entry.server_name,
            )
        )
        for binding in binding_result.scalars().all():
            binding.enabled = False

        catalog_entry.status = "revoked"

        # Remove from workspace pool (disconnects sessions immediately)
        from src.integrations.mcp_pool import get_workspace_pool

        pool = get_workspace_pool()
        if pool:
            await pool.remove_server(self._workspace_id, catalog_entry.server_name)

        return OnboardingResult(
            status="revoked",
            catalog_id=catalog_id,
        )
