"""MCP Bridge — delegates actions to external MCP servers via session pool.

Instead of a process-global singleton Client, uses UserMCPSessionPool for
per-user authenticated connections with circuit breaking, tool name
normalization, and workspace-aware routing.

Polling for event ingestion still uses the lightweight per-provider connectors
(gmail.py, calendar.py, etc.) since MCP servers don't emit our RawEvent format.
But all *write actions* (send_email, create_draft, create_issue, etc.) go through MCP.
"""

import logging
import os
from typing import Any

from src.integrations.session_pool import UserMCPSessionPool
from src.services.mcp_resilience import MCPCircuitBreaker

logger = logging.getLogger(__name__)

# Module-level session pool — initialized once at startup
_session_pool: UserMCPSessionPool | None = None
_circuit_breaker = MCPCircuitBreaker()


async def get_mcp_config() -> dict:
    """Build the mcpServers config dict from all active installations across workspaces.

    The MCP bridge is process-global (initialized at startup), so it aggregates
    all active installations. Per-workspace scoping happens at the pool layer.
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

            servers: dict[str, dict] = {}
            for inst in installations:
                if inst.server_name in servers:
                    continue  # deduplicate across workspaces

                server_cfg: dict = {
                    "transport": inst.transport,
                    "auth_provider": inst.auth_provider or "none",
                }

                if inst.transport == "stdio" and inst.command:
                    server_cfg["command"] = inst.command
                    if inst.args:
                        server_cfg["args"] = inst.args
                    # Resolve env vars
                    if inst.env_template:
                        env = {
                            k: v
                            for k, v in ((k, os.environ.get(k, "")) for k in inst.env_template)
                            if v
                        }
                        if env:
                            server_cfg["env"] = env

                elif inst.transport in ("sse", "streamable-http") and inst.remote_url:
                    server_cfg["url"] = inst.remote_url

                servers[inst.server_name] = server_cfg

            return {"mcpServers": servers}
    except Exception:
        logger.debug("Control plane unavailable, returning empty config")
        return {"mcpServers": {}}


async def initialize_mcp_bridge(
    oauth_manager: Any | None = None,
    timeout_seconds: float = 30,
) -> None:
    """Initialize the MCP session pool and workspace pool.

    Call once at app startup (e.g. in lifespan). The pool manages
    per-user sessions lazily. Skipped in test environments.
    """
    global _session_pool

    # Skip in test environments to avoid spawning MCP subprocesses
    if os.environ.get("PYTEST_CURRENT_TEST") or os.environ.get("JARVIS_SKIP_MCP_BRIDGE"):
        logger.debug("MCP bridge skipped (test environment)")
        return

    # Create session pool
    _session_pool = UserMCPSessionPool(
        oauth_manager=oauth_manager,
        circuit_breaker=_circuit_breaker,
    )

    # Create and initialize workspace pool from DB
    from src.integrations.mcp_pool import WorkspaceMCPPool, set_workspace_pool

    workspace_pool = WorkspaceMCPPool(session_pool=_session_pool)
    set_workspace_pool(workspace_pool)

    count = await workspace_pool.initialize_from_db()
    logger.info("MCP bridge initialized: %d servers from DB", count)


async def shutdown_mcp_bridge() -> None:
    """Gracefully shut down the workspace pool and session pool."""
    global _session_pool

    from src.integrations.mcp_pool import get_workspace_pool, set_workspace_pool

    pool = get_workspace_pool()
    if pool:
        await pool.shutdown()
        set_workspace_pool(None)

    if _session_pool:
        await _session_pool.shutdown()
        _session_pool = None


def get_session_pool() -> UserMCPSessionPool | None:
    """Get the session pool (for direct access by capability resolver)."""
    return _session_pool


def is_mcp_tool(tool_name: str, workspace_id: str = "") -> bool:
    """Check if a tool is available via MCP bridge (checks pool's tool registry)."""
    if not _session_pool:
        return False
    return _session_pool.is_pool_tool(tool_name, workspace_id=workspace_id)


def list_mcp_tools(workspace_id: str = "") -> list[dict]:
    """Return metadata for MCP tools, optionally scoped to a workspace."""
    if not _session_pool:
        return []
    return _session_pool.get_all_tool_metadata(workspace_id=workspace_id)


def get_mcp_tool_names() -> list[str]:
    """Return names of all available MCP tools (canonical names)."""
    if not _session_pool:
        return []
    return list(_session_pool.get_all_tools().keys())


async def call_mcp_tool(
    tool_name: str,
    arguments: dict[str, Any] | None = None,
    *,
    user_id: str = "",
    workspace_id: str = "",
) -> dict:
    """Call a tool on an external MCP server via the session pool.

    Args:
        tool_name: Canonical tool name (snake_case).
        arguments: Tool arguments.
        user_id: User whose OAuth token to use.
        workspace_id: Workspace context.

    Returns:
        Dict with either the result or an error.
    """
    if not _session_pool:
        logger.warning("[mcp:bridge] bridge not initialized for tool %s", tool_name)
        return {"status": "error", "error": "MCP bridge not initialized"}

    # Find which server provides this tool
    server_name = _session_pool.get_server_for_tool(tool_name, workspace_id=workspace_id)
    if not server_name:
        logger.warning("[mcp:bridge] no server found for tool %s", tool_name)
        return {"status": "error", "error": f"Unknown MCP tool: {tool_name}"}

    logger.info(
        "[mcp:bridge] %s → server=%s user=%s",
        tool_name,
        server_name,
        user_id[:16] if user_id else "none",
    )
    result = await _session_pool.call_tool(
        tool_name,
        arguments or {},
        user_id=user_id,
        server_name=server_name,
        workspace_id=workspace_id,
    )
    status = result.get("status", "unknown")
    logger.info("[mcp:bridge] %s ← status=%s", tool_name, status)
    return result


async def refresh_server_auth(
    server_name: str,
    user_id: str,
    workspace_id: str = "",
) -> None:
    """Force reconnect a server session after OAuth token refresh."""
    if _session_pool:
        await _session_pool.refresh_session(
            server_name,
            user_id,
            workspace_id=workspace_id,
        )


def get_bridge_health() -> dict:
    """Get health status for the MCP bridge."""
    if not _session_pool:
        return {"status": "inactive", "servers": {}}
    return {
        "status": "active",
        "servers": _session_pool.get_health(),
    }
