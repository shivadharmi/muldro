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

    @property
    def session_pool(self) -> UserMCPSessionPool:
        """Access the underlying session pool."""
        return self._session_pool

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
        """Load all active installations from DB and register them.

        Called at startup. Returns count of servers registered.
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
                http_servers: list[tuple[str, str, dict]] = []
                for inst in installations:
                    config = _installation_to_config(inst)
                    await self.add_server(
                        inst.workspace_id,
                        inst.server_name,
                        config,
                    )
                    count += 1
                    if config.get("transport") in ("sse", "streamable-http"):
                        http_servers.append((inst.workspace_id, inst.server_name, config))

                # Eagerly discover tools from HTTP MCP servers in parallel so
                # schemas are available before the first tool call. Per-call
                # timeouts live inside session_pool.discover_tools.
                async def _http_discover(ws_id: str, srv_name: str, cfg: dict) -> None:
                    try:
                        await self._session_pool.discover_tools(
                            srv_name, workspace_id=ws_id, config=cfg
                        )
                    except Exception as disc_err:
                        logger.warning(
                            "Tool discovery failed for HTTP server %s: %s",
                            srv_name,
                            disc_err,
                        )

                if http_servers:
                    await asyncio.gather(
                        *(_http_discover(w, s, c) for w, s, c in http_servers),
                        return_exceptions=True,
                    )

                logger.info("Loaded %d MCP servers from DB", count)

                # Eagerly discover tool schemas from stdio servers that have
                # OAuth tokens available. Creates a session per (user, server),
                # which spawns the subprocess and calls list_tools().
                await self._discover_stdio_schemas(installations)

                return count

        except Exception as e:
            logger.debug("Failed to load MCP servers from DB: %s", e)
            return 0

    async def _discover_stdio_schemas(self, installations: list) -> None:
        """Eagerly discover tool schemas from stdio MCP servers.

        Two categories:
        - Auth-free servers (auth_provider is None): spawned immediately, no token needed.
        - OAuth servers: require a user with a valid token for the auth_provider.

        Discovery runs in parallel; each per-server spawn is bounded by a 30s
        timeout so one hanging subprocess cannot stall the whole pass.
        """
        oauth_providers = {"github", "slack", "notion"}

        auth_free_servers: list[tuple[str, str]] = []
        oauth_servers: list[tuple[str, str, str]] = []
        for inst in installations:
            if inst.transport != "stdio":
                continue
            if inst.auth_provider is None:
                auth_free_servers.append((inst.workspace_id, inst.server_name))
            elif inst.auth_provider in oauth_providers:
                oauth_servers.append((inst.workspace_id, inst.server_name, inst.auth_provider))

        async def _discover_auth_free(ws_id: str, srv_name: str) -> None:
            try:
                # Auth-free servers need a user_id for session keying but no real token.
                # Use the workspace owner (first member) as the session key.
                user_id = await self._resolve_workspace_user(ws_id)
                if not user_id:
                    logger.debug("No user for workspace %s — skipping %s", ws_id[:16], srv_name)
                    return
                session = await asyncio.wait_for(
                    self._session_pool.get_or_create_session(
                        srv_name, user_id=user_id, workspace_id=ws_id
                    ),
                    timeout=30,
                )
                logger.info(
                    "Auth-free schema discovery for %s: %d tools",
                    srv_name,
                    len(session.tools),
                )
            except asyncio.TimeoutError:
                logger.warning("Schema discovery timed out for %s (30s)", srv_name)
            except Exception:
                logger.debug("Schema discovery failed for %s", srv_name, exc_info=True)

        if auth_free_servers:
            await asyncio.gather(
                *(_discover_auth_free(w, s) for w, s in auth_free_servers),
                return_exceptions=True,
            )

        # Discover OAuth servers (need a user with a valid token)
        if not oauth_servers:
            return

        oauth_mgr = self._session_pool._oauth_manager
        if not oauth_mgr:
            logger.debug("No OAuthManager — skipping OAuth stdio schema discovery")
            return

        async def _discover_oauth(ws_id: str, srv_name: str, auth_provider: str) -> None:
            try:
                from sqlalchemy import select

                from src.models.oauth_token import OAuthToken

                async with oauth_mgr._db_factory() as db:
                    result = await db.execute(
                        select(OAuthToken.user_id)
                        .where(OAuthToken.provider == auth_provider)
                        .limit(1)
                    )
                    row = result.first()

                if not row:
                    logger.debug(
                        "No OAuth token for %s — skipping schema discovery for %s",
                        auth_provider,
                        srv_name,
                    )
                    return

                user_id = row[0]
                session = await asyncio.wait_for(
                    self._session_pool.get_or_create_session(
                        srv_name, user_id=user_id, workspace_id=ws_id
                    ),
                    timeout=30,
                )
                logger.info(
                    "Stdio schema discovery for %s: %d tools (user=%s)",
                    srv_name,
                    len(session.tools),
                    user_id[:16],
                )
            except asyncio.TimeoutError:
                logger.warning("OAuth stdio schema discovery timed out for %s (30s)", srv_name)
            except Exception:
                logger.debug("Stdio schema discovery failed for %s", srv_name, exc_info=True)

        await asyncio.gather(
            *(_discover_oauth(w, s, p) for w, s, p in oauth_servers),
            return_exceptions=True,
        )

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
