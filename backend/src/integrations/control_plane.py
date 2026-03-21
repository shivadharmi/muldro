"""Integration Control Plane — DB-backed replacement for mcp_config.py.

Manages ConnectorInstallation records: CRUD, health checks, and server config
resolution. All MCP server configs are now workspace-scoped DB rows instead
of static env-driven functions.
"""

import logging
import os

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.connector_installation import ConnectorInstallation

logger = logging.getLogger(__name__)


class IntegrationControlPlane:
    """Manages connector installations for a workspace."""

    def __init__(self, db: AsyncSession, workspace_id: str):
        self._db = db
        self._workspace_id = workspace_id

    # ── Read ─────────────────────────────────────────────────────────

    async def list_installations(
        self, status: str | None = None, enabled_only: bool = False
    ) -> list[ConnectorInstallation]:
        stmt = select(ConnectorInstallation).where(
            ConnectorInstallation.workspace_id == self._workspace_id
        )
        if status:
            stmt = stmt.where(ConnectorInstallation.status == status)
        if enabled_only:
            stmt = stmt.where(ConnectorInstallation.enabled.is_(True))
        stmt = stmt.order_by(ConnectorInstallation.server_name)
        result = await self._db.execute(stmt)
        return list(result.scalars().all())

    async def get_installation(self, install_id: str) -> ConnectorInstallation | None:
        result = await self._db.execute(
            select(ConnectorInstallation).where(
                ConnectorInstallation.install_id == install_id,
                ConnectorInstallation.workspace_id == self._workspace_id,
            )
        )
        return result.scalar_one_or_none()

    async def get_by_server_name(self, server_name: str) -> ConnectorInstallation | None:
        result = await self._db.execute(
            select(ConnectorInstallation).where(
                ConnectorInstallation.workspace_id == self._workspace_id,
                ConnectorInstallation.server_name == server_name,
            )
        )
        return result.scalar_one_or_none()

    # ── Write ────────────────────────────────────────────────────────

    async def create_installation(
        self,
        user_id: str,
        server_name: str,
        display_name: str,
        transport: str = "stdio",
        command: str | None = None,
        args: list | None = None,
        env_template: dict | None = None,
        remote_url: str | None = None,
        trust_id: str | None = None,
        auth_provider: str | None = None,
        scopes_granted: list[str] | None = None,
        config: dict | None = None,
    ) -> ConnectorInstallation:
        installation = ConnectorInstallation(
            workspace_id=self._workspace_id,
            user_id=user_id,
            server_name=server_name,
            display_name=display_name,
            transport=transport,
            command=command,
            args=args,
            env_template=env_template,
            remote_url=remote_url,
            trust_id=trust_id,
            auth_provider=auth_provider,
            scopes_granted=scopes_granted,
            config=config,
        )
        self._db.add(installation)
        await self._db.flush()
        return installation

    async def delete_installation(self, install_id: str) -> bool:
        inst = await self.get_installation(install_id)
        if not inst:
            return False
        await self._db.delete(inst)
        await self._db.flush()
        return True

    async def pause_installation(self, install_id: str) -> bool:
        return await self._set_status(install_id, "paused")

    async def resume_installation(self, install_id: str) -> bool:
        return await self._set_status(install_id, "active")

    async def update_health(self, install_id: str, health: str) -> bool:
        result = await self._db.execute(
            update(ConnectorInstallation)
            .where(
                ConnectorInstallation.install_id == install_id,
                ConnectorInstallation.workspace_id == self._workspace_id,
            )
            .values(health_status=health)
        )
        await self._db.flush()
        return result.rowcount > 0

    async def _set_status(self, install_id: str, status: str) -> bool:
        result = await self._db.execute(
            update(ConnectorInstallation)
            .where(
                ConnectorInstallation.install_id == install_id,
                ConnectorInstallation.workspace_id == self._workspace_id,
            )
            .values(status=status)
        )
        await self._db.flush()
        return result.rowcount > 0

    # ── MCP config resolution ────────────────────────────────────────

    async def get_mcp_server_configs(self) -> dict:
        """Build fastmcp-compatible mcpServers config from DB installations.

        Replaces mcp_config.get_available_mcp_configs() entirely.
        Resolves env vars from the current process environment.
        """
        installations = await self.list_installations(status="active", enabled_only=True)
        servers: dict[str, dict] = {}

        for inst in installations:
            if inst.transport == "stdio" and inst.command:
                server_cfg: dict = {
                    "transport": "stdio",
                    "command": inst.command,
                }
                if inst.args:
                    server_cfg["args"] = inst.args
                # Resolve env vars from process environment
                env = self._resolve_env(inst.env_template)
                if env:
                    server_cfg["env"] = env
                servers[inst.server_name] = server_cfg

            elif inst.transport in ("sse", "streamable-http") and inst.remote_url:
                servers[inst.server_name] = {
                    "transport": inst.transport,
                    "url": inst.remote_url,
                }

        return {"mcpServers": servers}

    def _resolve_env(self, env_template: dict | None) -> dict[str, str]:
        """Resolve env var template against current process environment."""
        if not env_template:
            return {}
        resolved: dict[str, str] = {}
        for key, desc_or_default in env_template.items():
            value = os.environ.get(key, "")
            if value:
                resolved[key] = value
        return resolved

    # ── Health check ─────────────────────────────────────────────────

    async def check_health(self, install_id: str) -> str:
        """Run a basic health check on an installation. Returns health status."""
        inst = await self.get_installation(install_id)
        if not inst:
            return "unavailable"

        if inst.status != "active":
            return "unavailable"

        # For stdio transports, check if the command exists
        if inst.transport == "stdio" and inst.command:
            import shutil

            if not shutil.which(inst.command):
                await self.update_health(install_id, "degraded")
                return "degraded"

        # Check env vars are set
        if inst.env_template:
            missing = [k for k in inst.env_template if not os.environ.get(k)]
            if missing:
                await self.update_health(install_id, "degraded")
                return "degraded"

        await self.update_health(install_id, "healthy")
        return "healthy"

    async def check_all_health(self) -> dict[str, str]:
        """Check health of all active installations. Returns {server_name: status}."""
        installations = await self.list_installations(status="active", enabled_only=True)
        results: dict[str, str] = {}
        for inst in installations:
            results[inst.server_name] = await self.check_health(inst.install_id)
        return results
