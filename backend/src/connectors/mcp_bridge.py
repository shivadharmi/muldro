"""MCP Bridge — delegates actions to external MCP servers via session pool.

Instead of a process-global singleton Client, uses UserMCPSessionPool for
per-user authenticated connections with circuit breaking and workspace-aware
routing. Tool names are passed through verbatim — there is no normalization
layer; the real MCP name is the name everywhere.

Polling for event ingestion goes through this bridge too for gateway-backed
sources: gmail and calendar subclass GatewayConnector and reach provider data
via call_mcp_tool, translating the result into RawEvents themselves. Native
sources (slack, notion) still poll provider REST directly with an OAuth token,
and github is gateway-backed but not yet ported — OpenConnector exposes no
notifications action, so its perception is deferred and the poller skips it
non-permanently. All *write actions* go through MCP regardless.
"""

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Any

from src.integrations.session_pool import UserMCPSessionPool
from src.services.mcp_resilience import MCPCircuitBreaker

logger = logging.getLogger(__name__)

# Module-level session pool — initialized once at startup
_session_pool: UserMCPSessionPool | None = None
_circuit_breaker = MCPCircuitBreaker()
_discovery_failures: dict[str, dict] = {}


def record_discovery_failure(server_name: str, error: str) -> None:
    """Record a tool discovery failure for a server."""
    existing = _discovery_failures.get(server_name, {"count": 0})
    _discovery_failures[server_name] = {
        "error": error[:200],
        "count": existing["count"] + 1,
        "last_failure": datetime.now(timezone.utc).isoformat(),
    }


def clear_discovery_failure(server_name: str) -> None:
    """Clear discovery failure record after successful discovery."""
    _discovery_failures.pop(server_name, None)


async def initialize_mcp_bridge(
    oauth_manager: Any | None = None,
    *,
    timeout_seconds: float = 30,
) -> None:
    """Wire the session pool + local-process manager and register server configs.

    No eager tool discovery and no background tasks: sessions are created
    lazily on first use, and tool schemas are durable in the DB (lazily
    re-discovered per server on first agent build). Registration is a few cheap
    DB reads, bounded by ``timeout_seconds``. Skipped in test environments.
    """
    global _session_pool

    if os.environ.get("PYTEST_CURRENT_TEST") or os.environ.get("MULDRO_SKIP_MCP_BRIDGE"):
        logger.debug("MCP bridge skipped (test environment)")
        return None

    # Idempotent: if the pool is already wired (e.g. lifespan ran in this same
    # process, or the worker re-invoked us under the reload-split fix), return
    # early WITHOUT re-creating the pool or re-registering server configs.
    if _session_pool is not None:
        logger.debug("MCP bridge already initialized")
        return None

    from src.config.settings import get_settings

    _settings = get_settings()

    _session_pool = UserMCPSessionPool(
        oauth_manager=oauth_manager,
        circuit_breaker=_circuit_breaker,
        ttl_seconds=_settings.mcp_session_idle_ttl_s,
    )

    from src.integrations.mcp_pool import WorkspaceMCPPool, set_workspace_pool

    workspace_pool = WorkspaceMCPPool(session_pool=_session_pool)
    set_workspace_pool(workspace_pool)

    # Wire the local-process manager for managed_local servers (Google Workspace).
    try:
        from src.integrations.local_process_manager import (
            LocalMCPProcessManager,
            set_local_process_manager,
        )
        from src.integrations.local_servers import build_local_server_specs

        specs = build_local_server_specs(_settings)
        set_local_process_manager(
            LocalMCPProcessManager(specs=specs, ready_timeout=_settings.mcp_local_ready_timeout_s)
        )
    except Exception:
        logger.exception("Failed to wire LocalMCPProcessManager")

    # Preflight: warn if host runtimes for spawning MCP servers are missing.
    from src.integrations.runtime_preflight import check_mcp_runtimes

    check_mcp_runtimes(["uvx", "npx"])

    # Register all active server configs (no network/process I/O, no eager
    # discovery). Bounded so a slow DB cannot stall startup.
    try:
        count = await asyncio.wait_for(workspace_pool.initialize_from_db(), timeout=timeout_seconds)
        logger.info("MCP bridge ready: %d server configs registered", count)
    except asyncio.TimeoutError:
        logger.warning(
            "MCP config registration exceeded %.0fs — lazy on first use",
            timeout_seconds,
        )
    except Exception:
        logger.exception("MCP config registration failed")
    return None


async def shutdown_mcp_bridge() -> None:
    """Gracefully shut down the workspace pool, session pool, and local processes."""
    global _session_pool

    from src.integrations.mcp_pool import get_workspace_pool, set_workspace_pool

    pool = get_workspace_pool()
    if pool:
        await pool.shutdown()
        set_workspace_pool(None)

    if _session_pool:
        await _session_pool.shutdown()
        _session_pool = None

    from src.integrations.local_process_manager import (
        get_local_process_manager,
        set_local_process_manager,
    )

    mgr = get_local_process_manager()
    if mgr is not None:
        await mgr.shutdown()
        set_local_process_manager(None)


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


async def _resolve_server_from_registry(tool_name: str, workspace_id: str) -> str | None:
    """Look up the MCP server name for a tool from the DB registry.

    Used as fallback when no active session exists for the tool yet.
    Seed records carry the server name (e.g., "google-workspace") from
    EXTERNAL_TOOL_SEEDS, allowing session creation before first use.
    """
    try:
        from src.models.database import get_session_factory
        from src.services.tool_registry import ToolRegistry

        async with get_session_factory()() as db:
            registry = ToolRegistry(db, workspace_id=workspace_id or None)
            tool = await registry.get_tool(tool_name)
            if tool and tool.server:
                return tool.server
    except Exception:
        logger.debug("Registry lookup failed for %s", tool_name, exc_info=True)
    return None


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
        if os.environ.get("PYTEST_CURRENT_TEST"):
            skip_reason = "PYTEST_CURRENT_TEST set (test env)"
        elif os.environ.get("MULDRO_SKIP_MCP_BRIDGE"):
            skip_reason = "MULDRO_SKIP_MCP_BRIDGE set"
        else:
            skip_reason = "initialize_mcp_bridge() not called or raised"
        logger.warning(
            "[mcp:bridge] bridge not initialized for tool %s (reason=%s, discovery_failures=%d)",
            tool_name,
            skip_reason,
            len(_discovery_failures),
        )
        return {"status": "error", "error": "MCP bridge not initialized"}

    # Find which server provides this tool.
    # First check active sessions, then fall back to DB registry
    # (seeds know server names before any session is created).
    server_name = _session_pool.get_server_for_tool(tool_name, workspace_id=workspace_id)
    if not server_name:
        server_name = await _resolve_server_from_registry(tool_name, workspace_id)
    if not server_name:
        logger.warning("[mcp:bridge] no server found for tool %s", tool_name)
        return {"status": "error", "error": f"Unknown MCP tool: {tool_name}"}

    # Ensure server config is registered. Installations activated after
    # startup (e.g., via OAuth callback) won't be in the pool yet.
    # Reload from DB on demand so the first tool call succeeds.
    if not _session_pool.has_server_config(server_name, workspace_id):
        from src.integrations.mcp_pool import get_workspace_pool

        pool = get_workspace_pool()
        if pool:
            reloaded = await pool.reload_server(workspace_id, server_name)
            if reloaded:
                logger.info("[mcp:bridge] lazy-loaded config for %s", server_name)
            else:
                logger.warning(
                    "[mcp:bridge] no active installation for %s/%s",
                    workspace_id,
                    server_name,
                )
                return {
                    "status": "error",
                    "error": f"MCP server '{server_name}' not configured or not active",
                }

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


async def close_turn_sessions(keys: list[tuple[str, str, str]]) -> None:
    """Tear down the MCP sessions opened during a turn (called by TurnScope)."""
    if _session_pool and keys:
        await _session_pool.close_keys(keys)


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
        return {
            "status": "inactive",
            "servers": {},
            "discovery_failures": dict(_discovery_failures),
        }
    return {
        "status": "active",
        "servers": _session_pool.get_health(),
        "discovery_failures": dict(_discovery_failures),
    }
