"""MCP Bridge Connector — delegates actions to external MCP servers.

Instead of hand-rolling HTTP calls to Google/GitHub/Slack APIs, this connector
uses fastmcp.Client to call tools on external MCP servers (e.g. google-workspace-mcp,
@modelcontextprotocol/server-github, @anthropic/slack-mcp).

Polling for event ingestion still uses the lightweight per-provider connectors
(gmail.py, calendar.py, etc.) since MCP servers don't emit our RawEvent format.
But all *write actions* (send_email, create_draft, create_issue, etc.) go through MCP.
"""

import asyncio
import logging
import os
from typing import Any

from fastmcp import Client

from src.services.mcp_resilience import MCPCircuitBreaker

logger = logging.getLogger(__name__)

# Module-level singleton — initialized once at startup
_mcp_client: Client | None = None
_circuit_breaker = MCPCircuitBreaker()
_available_tools: dict[str, dict] = {}  # tool_name -> {server, schema}


async def get_mcp_config() -> dict:
    """Build the mcpServers config dict from all active installations across workspaces.

    The MCP bridge is process-global (initialized at startup), so it aggregates
    all active installations. Per-workspace scoping happens at the gateway/resolver layer.
    """
    import os

    from src.models.database import get_session_factory

    try:
        from sqlalchemy import select

        from src.models.connector_installation import ConnectorInstallation

        async with get_session_factory()() as db:
            result = await db.execute(
                select(ConnectorInstallation).where(
                    ConnectorInstallation.status == "active",
                    ConnectorInstallation.enabled.is_(True),
                    ConnectorInstallation.transport.in_(["stdio", "sse", "streamable-http"]),
                )
            )
            installations = result.scalars().all()

            servers: dict[str, dict] = {}
            for inst in installations:
                if inst.server_name in servers:
                    continue  # deduplicate across workspaces

                if inst.transport == "stdio" and inst.command:
                    server_cfg: dict = {
                        "transport": "stdio",
                        "command": inst.command,
                    }
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
                    servers[inst.server_name] = server_cfg

                elif inst.transport in ("sse", "streamable-http") and inst.remote_url:
                    servers[inst.server_name] = {
                        "transport": inst.transport,
                        "url": inst.remote_url,
                    }

            return {"mcpServers": servers}
    except Exception:
        logger.debug("Control plane unavailable, returning empty config")
        return {"mcpServers": {}}


async def initialize_mcp_bridge(timeout_seconds: float = 30) -> None:
    """Initialize the MCP bridge client and discover available tools.

    Call once at app startup (e.g. in lifespan). The client stays connected
    for the lifetime of the process. Skipped in test environments or if
    initialization exceeds timeout_seconds.
    """
    global _mcp_client, _available_tools

    # Skip in test environments to avoid spawning MCP subprocesses
    if os.environ.get("PYTEST_CURRENT_TEST") or os.environ.get("JARVIS_SKIP_MCP_BRIDGE"):
        logger.debug("MCP bridge skipped (test environment)")
        return

    config = await get_mcp_config()
    if not config["mcpServers"]:
        logger.info("No external MCP servers configured — MCP bridge inactive")
        return

    # Provide roots so MCP servers (e.g. filesystem) that request roots/list
    # get a valid response instead of "No active context found" error.
    workspace = os.environ.get("JARVIS_WORKSPACE_PATH", "/tmp/jarvis-workspace")
    _mcp_client = Client(config, roots=[workspace])
    try:
        await asyncio.wait_for(_connect_and_discover(config), timeout=timeout_seconds)
    except asyncio.TimeoutError:
        logger.warning("MCP bridge initialization timed out after %.0fs", timeout_seconds)
        _mcp_client = None
    except Exception:
        logger.warning("MCP bridge initialization failed", exc_info=True)
        _mcp_client = None


async def _connect_and_discover(config: dict) -> None:
    """Connect to MCP servers and discover tools."""
    global _available_tools

    await _mcp_client.__aenter__()
    tools = await _mcp_client.list_tools()
    _available_tools = {
        tool.name: {
            "description": tool.description or "",
            "input_schema": tool.inputSchema if hasattr(tool, "inputSchema") else {},
        }
        for tool in tools
    }

    # Register discovered MCP tools in ToolRegistry (best-effort)
    await _register_discovered_tools()

    logger.info(
        "MCP bridge initialized: %d tools from %d servers",
        len(_available_tools),
        len(config["mcpServers"]),
    )
    logger.debug("MCP tools: %s", list(_available_tools.keys()))


async def _register_discovered_tools() -> None:
    """Auto-register MCP-discovered tools in ToolRegistry if not already present.

    Known tools (in TOOL_TO_CAPABILITY) keep their cataloged risk/approval settings.
    Unknown tools are registered as disabled with requires_approval=True to prevent
    unvetted MCP tools from executing without review.
    """
    try:
        from src.integrations.capabilities import TOOL_TO_CAPABILITY
        from src.models.database import get_session_factory
        from src.services.tool_registry import ToolRegistry

        async with get_session_factory()() as db:
            registry = ToolRegistry(db)
            registered = 0
            for tool_name, meta in _available_tools.items():
                existing = await registry.get_tool(tool_name)
                if not existing:
                    is_known = tool_name in TOOL_TO_CAPABILITY
                    await registry.register_tool(
                        name=tool_name,
                        risk_level="low" if is_known else "unknown",
                        requires_approval=not is_known,
                        description=meta.get("description", ""),
                    )
                    if not is_known:
                        # Disable unknown tools — admin must explicitly enable
                        tool = await registry.get_tool(tool_name)
                        if tool:
                            tool.enabled = False
                            await db.flush()
                    registered += 1
            if registered:
                await db.commit()
                logger.info("Auto-registered %d MCP tools in ToolRegistry", registered)
    except Exception:
        logger.debug("MCP tool auto-registration skipped", exc_info=True)


async def shutdown_mcp_bridge() -> None:
    """Gracefully shut down the MCP bridge client."""
    global _mcp_client, _available_tools
    if _mcp_client:
        try:
            await _mcp_client.__aexit__(None, None, None)
        except Exception:
            logger.debug("MCP bridge shutdown error", exc_info=True)
        _mcp_client = None
        _available_tools = {}


def is_mcp_tool(tool_name: str) -> bool:
    """Check if a tool is available via MCP bridge."""
    return tool_name in _available_tools


def list_mcp_tools() -> list[dict]:
    """Return metadata for all discovered MCP tools."""
    return [{"name": name, **meta} for name, meta in _available_tools.items()]


def get_mcp_tool_names() -> list[str]:
    """Return names of all available MCP tools."""
    return list(_available_tools.keys())


async def call_mcp_tool(tool_name: str, arguments: dict[str, Any] | None = None) -> dict:
    """Call a tool on an external MCP server via the bridge.

    Returns a dict with either the result or an error.
    Uses circuit breaker for resilience.
    """
    if not _mcp_client:
        return {"status": "error", "error": "MCP bridge not initialized"}

    if tool_name not in _available_tools:
        return {"status": "error", "error": f"Unknown MCP tool: {tool_name}"}

    # Derive the server name from the namespaced tool (e.g. "google-workspace_gmail_send")
    server_name = _derive_server_name(tool_name)

    if not _circuit_breaker.is_available(server_name):
        return {
            "status": "error",
            "error": f"MCP server '{server_name}' circuit open (too many failures)",
        }

    try:
        result = await _mcp_client.call_tool(tool_name, arguments or {})
        _circuit_breaker.record_success(server_name)

        # FastMCP call_tool returns a CallToolResult — extract content
        if hasattr(result, "content"):
            # result.content is a list of content blocks
            text_parts = []
            for block in result.content:
                if hasattr(block, "text"):
                    text_parts.append(block.text)
                elif hasattr(block, "data"):
                    text_parts.append(str(block.data))
            return {"status": "ok", "result": "\n".join(text_parts)}

        # If it's already a simple type
        return {"status": "ok", "result": str(result)}

    except Exception as e:
        _circuit_breaker.record_failure(server_name)
        logger.warning("MCP tool '%s' failed: %s", tool_name, e, exc_info=True)
        return {"status": "error", "error": f"MCP call failed: {e}"}


def _derive_server_name(tool_name: str) -> str:
    """Derive the MCP server name from a namespaced tool name.

    FastMCP namespaces tools as `servername_toolname` when using multi-server config.
    """
    return tool_name.split("_")[0]
