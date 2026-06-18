"""Workspace MCP Pool — per-workspace server management with dynamic add/remove.

Manages MCP server configurations per workspace. Servers are registered
from IntegrationInstallation records and can be added/removed at runtime
without process restart. Delegates actual connections to UserMCPSessionPool.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from typing import Any

from src.integrations.session_pool import UserMCPSessionPool

logger = logging.getLogger(__name__)


@dataclass
class ServerEntry:
    """A registered MCP server for a workspace."""

    server_name: str
    workspace_id: str
    config: dict
    tools: dict[str, str] = field(default_factory=dict)  # canonical → raw
    health_status: str = "unknown"


class WorkspaceMCPPool:
    """Per-workspace MCP server pool with dynamic add/remove.

    Manages server configurations and delegates connection management
    to UserMCPSessionPool for per-user auth'd sessions.
    """

    def __init__(
        self,
        session_pool: UserMCPSessionPool,
    ) -> None:
        self._session_pool = session_pool
        # workspace_id → {server_name → ServerEntry}
        self._workspaces: dict[str, dict[str, ServerEntry]] = {}
        self._lock = asyncio.Lock()
        # (workspace_id, server_name) pairs that completed a successful discovery pass
        # in this process. The DB persists schemas across restarts, so once-per-process
        # is correct: a server that responded is never re-probed in the same process.
        self._discovered_servers: set[tuple[str, str]] = set()

    @property
    def session_pool(self) -> UserMCPSessionPool:
        """Access the underlying session pool."""
        return self._session_pool

    def is_discovered(self, server_name: str, workspace_id: str = "") -> bool:
        """True if a discovery pass already succeeded for this server in-process."""
        return (workspace_id, server_name) in self._discovered_servers

    async def add_server(
        self,
        workspace_id: str,
        server_name: str,
        config: dict,
    ) -> ServerEntry:
        """Register an MCP server for a workspace (no restart required).

        The server config is stored and registered with the session pool
        for lazy connection on first tool call.
        """
        async with self._lock:
            if workspace_id not in self._workspaces:
                self._workspaces[workspace_id] = {}

            # Remove existing entry if re-adding
            if server_name in self._workspaces[workspace_id]:
                await self._remove_server_unlocked(workspace_id, server_name)

            entry = ServerEntry(
                server_name=server_name,
                workspace_id=workspace_id,
                config=config,
            )
            self._workspaces[workspace_id][server_name] = entry

            # Register with session pool for lazy connection
            self._session_pool.register_server_config(
                server_name,
                config,
                workspace_id=workspace_id,
            )

            logger.info(
                "Added MCP server: workspace=%s server=%s transport=%s",
                workspace_id,
                server_name,
                config.get("transport", "unknown"),
            )
            return entry

    async def remove_server(
        self,
        workspace_id: str,
        server_name: str,
    ) -> bool:
        """Remove an MCP server from a workspace and disconnect all sessions."""
        async with self._lock:
            return await self._remove_server_unlocked(workspace_id, server_name)

    async def _remove_server_unlocked(
        self,
        workspace_id: str,
        server_name: str,
    ) -> bool:
        """Internal remove without lock (caller must hold _lock)."""
        ws = self._workspaces.get(workspace_id, {})
        entry = ws.pop(server_name, None)
        if not entry:
            return False

        # Disconnect all user sessions for this server in this workspace
        sessions_to_close = [
            key
            for key in self._session_pool._sessions
            if key[0] == workspace_id and key[1] == server_name
        ]
        for key in sessions_to_close:
            try:
                await self._session_pool.refresh_session(
                    key[1],
                    key[2],
                    workspace_id=key[0],
                )
            except Exception:
                logger.warning("Failed to close session %s during server removal", key)

        # Remove config, tool mappings, and metadata so the server cannot be
        # rediscovered or reconnected on subsequent tool calls.
        self._session_pool.unregister_server(server_name, workspace_id=workspace_id)

        logger.info(
            "Removed MCP server: workspace=%s server=%s",
            workspace_id,
            server_name,
        )
        return True

    async def reload_server(
        self,
        workspace_id: str,
        server_name: str,
    ) -> ServerEntry | None:
        """Reload a server config from DB and reconnect.

        Fetches fresh IntegrationInstallation from DB, re-registers config.
        """
        from src.models.database import get_session_factory

        try:
            from sqlalchemy import select

            from src.models.integration_installation import IntegrationInstallation

            async with get_session_factory()() as db:
                result = await db.execute(
                    select(IntegrationInstallation).where(
                        IntegrationInstallation.workspace_id == workspace_id,
                        IntegrationInstallation.server_name == server_name,
                        IntegrationInstallation.status == "active",
                        IntegrationInstallation.enabled.is_(True),
                    )
                )
                inst = result.scalar_one_or_none()
                if not inst:
                    logger.warning(
                        "Cannot reload: no active installation for %s/%s",
                        workspace_id,
                        server_name,
                    )
                    return None

                config = _installation_to_config(inst)
                return await self.add_server(workspace_id, server_name, config)

        except Exception as e:
            logger.error("Failed to reload server %s: %s", server_name, e)
            return None

    async def discover_and_persist(
        self,
        server_name: str,
        *,
        workspace_id: str,
    ) -> int:
        """Spawn one short-lived discovery session, persist schemas, tear down.

        Used by lazy discovery. Ensures the server config is registered, finds
        a user for auth keying, opens a session (which calls list_tools and
        persists discovered schemas via _register_discovered_tools), then closes
        it immediately so nothing is left running. Returns tool count.
        """
        if not self._session_pool.has_server_config(server_name, workspace_id):
            if not await self.reload_server(workspace_id, server_name):
                return 0

        user_id = await self._resolve_workspace_user(workspace_id)
        if not user_id:
            return 0

        try:
            session = await self._session_pool.get_or_create_session(
                server_name, user_id=user_id, workspace_id=workspace_id
            )
            count = len(session.tools)
            # Mark discovered only after the server actually responded. Transient
            # failures (exception path) are NOT marked so they are retried next time.
            self._discovered_servers.add((workspace_id, server_name))
        except Exception:
            logger.debug("discover_and_persist failed for %s", server_name, exc_info=True)
            return 0
        finally:
            try:
                await self._session_pool.refresh_session(
                    server_name, user_id, workspace_id=workspace_id
                )
            except Exception:
                logger.debug("teardown after discovery failed for %s", server_name)
        return count

    def get_servers(self, workspace_id: str) -> list[dict]:
        """List all servers for a workspace with their status."""
        ws = self._workspaces.get(workspace_id, {})
        return [
            {
                "server_name": entry.server_name,
                "transport": entry.config.get("transport", "unknown"),
                "auth_provider": entry.config.get("auth_provider", "none"),
                "health_status": entry.health_status,
                "tool_count": len(entry.tools),
            }
            for entry in ws.values()
        ]

    def get_all_tools(self, workspace_id: str) -> dict[str, str]:
        """Return all tools for a workspace: {canonical_name: server_name}."""
        result: dict[str, str] = {}
        ws = self._workspaces.get(workspace_id, {})
        for server_name, entry in ws.items():
            for canonical in entry.tools:
                result[canonical] = server_name
        # Also include tools from the session pool (discovered at runtime)
        for tool_name, server_name in self._session_pool.get_all_tools(
            workspace_id=workspace_id,
        ).items():
            if tool_name not in result:
                result[tool_name] = server_name
        return result

    async def initialize_from_db(self) -> int:
        """Register all active installations from DB. No network/process I/O.

        Tool schemas are no longer discovered eagerly — they come from the DB
        registry (durable) and are lazily (re)discovered on first agent build
        via discover_and_persist. Returns count of servers registered.
        """
        from src.models.database import get_session_factory

        try:
            from sqlalchemy import select

            from src.models.integration_installation import IntegrationInstallation

            async with get_session_factory()() as db:
                result = await db.execute(
                    select(IntegrationInstallation).where(
                        IntegrationInstallation.status == "active",
                        IntegrationInstallation.enabled.is_(True),
                        IntegrationInstallation.transport.in_(["stdio", "sse", "streamable-http"]),
                    )
                )
                installations = result.scalars().all()

                count = 0
                for inst in installations:
                    config = _installation_to_config(inst)
                    await self.add_server(inst.workspace_id, inst.server_name, config)
                    count += 1

                logger.info("Registered %d MCP server configs from DB (no discovery)", count)
                return count
        except Exception as e:
            logger.debug("Failed to register MCP servers from DB: %s", e)
            return 0

    async def _resolve_workspace_user(self, workspace_id: str) -> str | None:
        """Find any user in a workspace for session keying (auth-free servers)."""
        from sqlalchemy import select

        from src.models.users import WorkspaceMember

        oauth_mgr = self._session_pool._oauth_manager
        if oauth_mgr:
            async with oauth_mgr._db_factory() as db:
                result = await db.execute(
                    select(WorkspaceMember.user_id)
                    .where(WorkspaceMember.workspace_id == workspace_id)
                    .limit(1)
                )
                row = result.first()
                return row[0] if row else None
        return None

    async def health_check_all(self) -> dict[str, str]:
        """Ping all registered servers and update health_status.

        Returns {server_name: "healthy"|"unhealthy"} for each server.
        """
        results: dict[str, str] = {}
        for ws_id, servers in self._workspaces.items():
            for server_name, entry in servers.items():
                try:
                    tools = self._session_pool.get_all_tools(workspace_id=ws_id)
                    status = "healthy" if tools else "unhealthy"
                    entry.health_status = status
                except Exception:
                    entry.health_status = "unhealthy"
                    logger.debug("MCP health check failed: %s/%s", ws_id, server_name)
                results[f"{ws_id}/{server_name}"] = entry.health_status
        return results

    async def shutdown(self) -> None:
        """Shut down all sessions across all workspaces."""
        await self._session_pool.shutdown()
        async with self._lock:
            self._workspaces.clear()
        logger.info("WorkspaceMCPPool shut down")


def _installation_to_config(inst: Any) -> dict:
    """Convert a IntegrationInstallation ORM object to a config dict."""
    config: dict = {
        "transport": inst.transport,
        "auth_provider": inst.auth_provider or "none",
    }

    if inst.transport == "stdio" and inst.command:
        config["command"] = inst.command
        if inst.args:
            config["args"] = inst.args
        if inst.env_template:
            env = {k: v for k, v in ((k, os.environ.get(k, "")) for k in inst.env_template) if v}
            if env:
                config["env"] = env

    elif inst.transport in ("sse", "streamable-http") and inst.remote_url:
        config["url"] = inst.remote_url

    # Carry tool_defaults from the installation's JSONB config into the
    # session-pool config. UserMCPSessionPool.call_tool merges these into
    # every MCP tool_input when the matching key is absent — this is how
    # Atlassian's cloudId (and any other per-workspace constant) reaches
    # the MCP server without the agent needing to know it.
    inst_cfg = getattr(inst, "config", None) or {}
    if isinstance(inst_cfg, dict):
        defaults = inst_cfg.get("tool_defaults")
        if isinstance(defaults, dict) and defaults:
            config["tool_defaults"] = defaults

    if isinstance(inst_cfg, dict) and inst_cfg.get("managed_local"):
        config["managed_local"] = True

    return config


# Module-level singleton
_workspace_pool: WorkspaceMCPPool | None = None


def get_workspace_pool() -> WorkspaceMCPPool | None:
    """Get the global WorkspaceMCPPool singleton."""
    return _workspace_pool


def set_workspace_pool(pool: WorkspaceMCPPool) -> None:
    """Set the global WorkspaceMCPPool singleton (called at startup)."""
    global _workspace_pool
    _workspace_pool = pool
