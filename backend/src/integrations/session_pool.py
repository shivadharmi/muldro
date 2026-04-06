"""Per-user MCP session pool — manages authenticated Client instances.

Each (workspace_id, server_name, user_id) triple gets its own Client with
the user's OAuth token injected. Sessions are lazily created and TTL-cleaned.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from fastmcp import Client
from fastmcp.client.auth import BearerAuth

from src.services.mcp_resilience import MCPCircuitBreaker

logger = logging.getLogger(__name__)

# Default idle timeout before a session is cleaned up (30 minutes)
SESSION_TTL_SECONDS = 1800

# Mapping: server_name → env var name for stdio token injection.
# Google Workspace excluded — it uses file-based auth, not raw tokens.
#
# Security note: Tokens are injected via environment variables because the
# MCP stdio transport protocol requires servers to be spawned as subprocesses.
# Env vars are visible in `ps aux` output — accepted trade-off because
# stdin-based token passing would break MCP server compatibility.
# The sessions are short-lived (30-min TTL) and per-user.
_STDIO_TOKEN_ENV_VARS: dict[str, str] = {
    "github": "GITHUB_PERSONAL_ACCESS_TOKEN",
    "slack": "SLACK_MCP_XOXB_TOKEN",
    "linear": "LINEAR_ACCESS_TOKEN",
    "notion": "NOTION_TOKEN",
}


@dataclass
class SessionEntry:
    """A tracked MCP client session."""

    client: Client
    client_ctx: Any  # The async context manager
    server_name: str
    user_id: str
    tools: dict[str, str]  # canonical_name → raw_mcp_name
    created_at: float = field(default_factory=time.monotonic)
    last_used: float = field(default_factory=time.monotonic)


class UserMCPSessionPool:
    """Pool of per-user MCP Client instances with auth and circuit breaking.

    Usage:
        pool = UserMCPSessionPool(oauth_manager, circuit_breaker)
        result = await pool.call_tool(
            "gmail_send", {...}, user_id="usr_1", server_name="google-workspace",
        )
    """

    def __init__(
        self,
        oauth_manager: Any | None = None,
        circuit_breaker: MCPCircuitBreaker | None = None,
        ttl_seconds: float = SESSION_TTL_SECONDS,
    ) -> None:
        self._oauth_manager = oauth_manager
        self._circuit_breaker = circuit_breaker or MCPCircuitBreaker()
        self._ttl_seconds = ttl_seconds
        # (workspace_id, server_name, user_id) → SessionEntry
        self._sessions: dict[tuple[str, str, str], SessionEntry] = {}
        self._lock = asyncio.Lock()
        # (workspace_id, server_name) → config dict
        self._server_configs: dict[tuple[str, str], dict] = {}
        # (workspace_id, server_name) → tool mapping (canonical → raw)
        self._server_tools: dict[tuple[str, str], dict[str, str]] = {}
        # canonical_name → metadata (global — names don't conflict across workspaces)
        self._tool_metadata: dict[str, dict[str, Any]] = {}

    def register_server_config(
        self,
        server_name: str,
        config: dict,
        workspace_id: str = "",
    ) -> None:
        """Register server configuration for later session creation."""
        self._server_configs[(workspace_id, server_name)] = config

    async def get_or_create_session(
        self,
        server_name: str,
        user_id: str,
        workspace_id: str = "",
    ) -> SessionEntry:
        """Get an existing session or create a new one with auth."""
        key = (workspace_id, server_name, user_id)

        async with self._lock:
            entry = self._sessions.get(key)
            if entry:
                entry.last_used = time.monotonic()
                return entry

            # Create new session
            config = self._server_configs.get((workspace_id, server_name))
            if not config:
                raise RuntimeError(
                    f"No config registered for MCP server '{server_name}'. "
                    f"Call register_server_config() first."
                )

            # Shallow copy to avoid mutating the registered template
            config = dict(config)
            if "env" in config:
                config["env"] = dict(config["env"])

            # Resolve auth
            auth = await self._resolve_auth(server_name, user_id, config)

            # Create Client
            transport = config.get("transport", "stdio")
            if transport in ("sse", "streamable-http"):
                url = config["url"]
                client_ctx = Client(url, auth=auth) if auth else Client(url)
            else:
                # stdio transport — inject auth as env var, then build config
                if auth and isinstance(auth, BearerAuth):
                    _inject_stdio_auth(config, server_name, auth.token)
                server_cfg = {"mcpServers": {server_name: config}}
                client_ctx = Client(server_cfg)

            # Connect and discover tools
            client = await client_ctx.__aenter__()
            raw_tools = await client.list_tools()

            # Skip normalization — store real MCP names end-to-end
            tool_mapping = {}
            for t in raw_tools:
                tool_mapping[t.name] = t.name  # identity mapping
                input_schema = (
                    getattr(t, "inputSchema", None)
                    or getattr(t, "input_schema", None)
                    or {"type": "object", "properties": {}}
                )
                self._tool_metadata[t.name] = {
                    "name": t.name,
                    "server": server_name,
                    "description": t.description or "",
                    "input_schema": input_schema,
                    "_workspace_id": workspace_id,
                }
            self._server_tools[(workspace_id, server_name)] = tool_mapping

            # Register unknown discovered tools in DB with safe defaults
            await self._register_discovered_tools(raw_tools, server_name, workspace_id)

            entry = SessionEntry(
                client=client,
                client_ctx=client_ctx,
                server_name=server_name,
                user_id=user_id,
                tools=tool_mapping,
            )
            self._sessions[key] = entry

            logger.info(
                "Created MCP session: server=%s user=%s tools=%d",
                server_name,
                user_id,
                len(tool_mapping),
            )
            return entry

    async def _register_discovered_tools(
        self, raw_tools: list, server_name: str, workspace_id: str
    ) -> None:
        """Register or enrich discovered tools in DB.

        New tools get capability=None (invisible to agents until admin maps
        capability). Existing tools (e.g., from seeds) get enriched with
        input_schema and description from MCP discovery — seeds have
        capability but lack schema; discovery provides schema.
        """
        try:
            from ulid import ULID

            from src.models.database import get_session_factory
            from src.models.tool_definitions import ToolDefinition
            from src.services.tool_registry import ToolRegistry

            async with get_session_factory()() as db:
                registry = ToolRegistry(db, workspace_id=workspace_id or None)
                for t in raw_tools:
                    discovered_schema = (
                        getattr(t, "inputSchema", None)
                        or getattr(t, "input_schema", None)
                        or {"type": "object", "properties": {}}
                    )
                    discovered_desc = t.description or ""

                    existing = await registry.get_tool(t.name)
                    if not existing:
                        new_tool = ToolDefinition(
                            tool_id=f"tool_{ULID()}",
                            workspace_id=workspace_id or None,
                            name=t.name,
                            server=server_name,
                            backend="external_mcp",
                            source="discovered",
                            capability=None,
                            risk_level="medium",
                            requires_approval=True,
                            description=discovered_desc,
                            input_schema=discovered_schema,
                            enabled=True,
                            verified=True,
                        )
                        db.add(new_tool)
                        logger.info(
                            "Registered discovered tool: %s from %s",
                            t.name,
                            server_name,
                        )
                    else:
                        # Enrich existing seed record with discovered
                        # schema/description (seeds lack these fields)
                        enriched = False
                        if not existing.input_schema and discovered_schema:
                            existing.input_schema = discovered_schema
                            enriched = True
                        if not existing.description and discovered_desc:
                            existing.description = discovered_desc
                            enriched = True
                        if enriched:
                            logger.info(
                                "Enriched tool %s with discovered metadata",
                                t.name,
                            )
                await db.commit()
        except Exception:
            logger.debug("Failed to register discovered tools", exc_info=True)

    async def call_tool(
        self,
        tool_name: str,
        tool_input: dict,
        *,
        user_id: str,
        server_name: str,
        workspace_id: str = "",
        max_retries: int = 3,
    ) -> dict:
        """Call a tool on an external MCP server with auth, circuit breaking, and retry.

        Retries transient errors (timeout, rate limit, server error) with
        exponential backoff + jitter. Does not retry auth or validation errors.
        """
        import asyncio
        import random

        from src.integrations.mcp_errors import (
            classify_error,
            is_transient,
            make_error_response,
        )

        # Circuit breaker check
        if not self._circuit_breaker.is_available(server_name):
            logger.warning(
                "[mcp:session] circuit OPEN for %s — rejecting %s",
                server_name,
                tool_name,
            )
            return {
                "status": "error",
                "error_code": "circuit_open",
                "message": f"MCP server '{server_name}' circuit open",
            }

        # Get or create session
        try:
            session = await self.get_or_create_session(
                server_name,
                user_id,
                workspace_id,
            )
        except Exception as e:
            logger.warning(
                "[mcp:session] session creation failed for %s/%s: %s",
                server_name,
                tool_name,
                e,
            )
            return make_error_response(e, tool_name=tool_name)

        # Resolve canonical → raw MCP tool name
        raw_name = tool_name

        import time as _time

        call_start = _time.monotonic()
        last_error: Exception | None = None
        for attempt in range(max_retries):
            try:
                result = await session.client.call_tool(raw_name, tool_input)
                self._circuit_breaker.record_success(server_name)
                session.last_used = time.monotonic()
                latency_ms = int((_time.monotonic() - call_start) * 1000)

                # Extract content from CallToolResult
                if hasattr(result, "content"):
                    text_parts = []
                    for block in result.content:
                        if hasattr(block, "text"):
                            text_parts.append(block.text)
                        elif hasattr(block, "data"):
                            text_parts.append(str(block.data))
                    output = "\n".join(text_parts)
                    logger.info(
                        "[mcp:session] %s on %s OK | %dms | %d chars",
                        tool_name,
                        server_name,
                        latency_ms,
                        len(output),
                    )
                    return {"status": "ok", "result": output}

                logger.info(
                    "[mcp:session] %s on %s OK | %dms",
                    tool_name,
                    server_name,
                    latency_ms,
                )
                return {"status": "ok", "result": str(result)}

            except Exception as e:
                last_error = e
                error_code = classify_error(e)

                # Only retry transient errors
                if not is_transient(error_code) or attempt >= max_retries - 1:
                    self._circuit_breaker.record_failure(server_name)
                    break

                # Exponential backoff with jitter: 1s, 2s, 4s
                delay = (2**attempt) + random.uniform(0, 0.5)
                logger.info(
                    "Retrying MCP tool '%s' (attempt %d/%d, %s), delay=%.1fs",
                    tool_name,
                    attempt + 1,
                    max_retries,
                    error_code,
                    delay,
                )
                await asyncio.sleep(delay)

        logger.warning(
            "MCP tool '%s' on '%s' failed after %d attempts: %s",
            tool_name,
            server_name,
            max_retries,
            last_error,
        )
        return make_error_response(
            last_error or RuntimeError("Unknown error"),
            tool_name=tool_name,
        )

    async def refresh_session(
        self,
        server_name: str,
        user_id: str,
        workspace_id: str = "",
    ) -> None:
        """Force reconnect a session (e.g., after OAuth token refresh)."""
        key = (workspace_id, server_name, user_id)

        async with self._lock:
            entry = self._sessions.pop(key, None)
            if entry:
                try:
                    await entry.client_ctx.__aexit__(None, None, None)
                except Exception:
                    logger.debug("Error closing session %s/%s", server_name, user_id)

        logger.info("Refreshed MCP session: server=%s user=%s", server_name, user_id)

    async def cleanup_idle(self) -> int:
        """Remove sessions that have been idle beyond TTL. Returns count removed."""
        now = time.monotonic()
        to_remove: list[tuple[str, str, str]] = []

        async with self._lock:
            for key, entry in self._sessions.items():
                if now - entry.last_used > self._ttl_seconds:
                    to_remove.append(key)

            for key in to_remove:
                entry = self._sessions.pop(key)
                try:
                    await entry.client_ctx.__aexit__(None, None, None)
                except Exception:
                    pass

        if to_remove:
            logger.info("Cleaned up %d idle MCP sessions", len(to_remove))
        return len(to_remove)

    async def shutdown(self) -> None:
        """Gracefully close all sessions."""
        async with self._lock:
            for key, entry in list(self._sessions.items()):
                try:
                    await entry.client_ctx.__aexit__(None, None, None)
                except Exception:
                    pass
            self._sessions.clear()
        logger.info("MCP session pool shut down")

    def unregister_server(self, server_name: str, workspace_id: str = "") -> None:
        """Remove all config, tool mappings, and metadata for a server.

        Called when a server is revoked so it cannot be rediscovered or reconnected.
        """
        self._server_configs.pop((workspace_id, server_name), None)
        removed_tools = self._server_tools.pop((workspace_id, server_name), {})
        for canonical in removed_tools:
            self._tool_metadata.pop(canonical, None)

    def is_pool_tool(self, tool_name: str, workspace_id: str = "") -> bool:
        """Check if a tool is known to any server in the pool."""
        for key, server_tools in self._server_tools.items():
            if workspace_id and key[0] != workspace_id:
                continue
            if tool_name in server_tools:
                return True
        return False

    def get_server_for_tool(self, tool_name: str, workspace_id: str = "") -> str | None:
        """Find which server provides a canonical tool name."""
        for key, tools in self._server_tools.items():
            if workspace_id and key[0] != workspace_id:
                continue
            if tool_name in tools:
                return key[1]  # server_name
        return None

    def get_all_tools(self, workspace_id: str = "") -> dict[str, str]:
        """Return all tools across all servers: {canonical_name: server_name}."""
        result: dict[str, str] = {}
        for key, tools in self._server_tools.items():
            if workspace_id and key[0] != workspace_id:
                continue
            for canonical in tools:
                result[canonical] = key[1]  # server_name
        return result

    def get_all_tool_metadata(self, workspace_id: str = "") -> list[dict[str, Any]]:
        """Return all tool metadata across servers."""
        result: list[dict[str, Any]] = []
        for name, meta in self._tool_metadata.items():
            if workspace_id and meta.get("_workspace_id") and meta["_workspace_id"] != workspace_id:
                continue
            item = dict(meta)
            item["name"] = name
            result.append(item)
        return result

    def get_health(self) -> dict[str, dict]:
        """Get health status for all servers in the pool."""
        health: dict[str, dict] = {}
        for (workspace_id, server_name, user_id), entry in self._sessions.items():
            if server_name not in health:
                health[server_name] = {
                    "sessions": 0,
                    "tools": len(entry.tools),
                    "circuit_available": self._circuit_breaker.is_available(server_name),
                }
            health[server_name]["sessions"] += 1
        return health

    async def _resolve_auth(
        self,
        server_name: str,
        user_id: str,
        config: dict,
    ) -> BearerAuth | str | None:
        """Resolve authentication for a server connection."""
        auth_provider = config.get("auth_provider", "none")

        if auth_provider == "none":
            return None

        if auth_provider == "token":
            # Static token from config
            token = config.get("token", "")
            return BearerAuth(token=token) if token else None

        if auth_provider in ("oauth", "google", "github", "slack", "linear", "notion", "jira"):
            # Resolve OAuth token from OAuthManager
            if not self._oauth_manager:
                logger.warning("OAuth requested but no OAuthManager configured")
                return None

            # Map server auth_provider to OAuth provider name
            provider_name = (
                auth_provider if auth_provider != "oauth" else _infer_provider(server_name)
            )
            try:
                token = await self._oauth_manager.get_valid_token(user_id, provider_name)
                if token:
                    return BearerAuth(token=token)
                logger.warning("No OAuth token for user=%s provider=%s", user_id, provider_name)
            except Exception as e:
                logger.warning("OAuth token resolution failed: %s", e)

        return None


def _infer_provider(server_name: str) -> str:
    """Infer the OAuth provider from the MCP server name."""
    name_lower = server_name.lower().replace("-", "_")
    if "google" in name_lower or "gmail" in name_lower or "calendar" in name_lower:
        return "google"
    if "github" in name_lower:
        return "github"
    if "slack" in name_lower:
        return "slack"
    if "linear" in name_lower:
        return "linear"
    if "notion" in name_lower:
        return "notion"
    if "jira" in name_lower or "atlassian" in name_lower:
        return "jira"
    return server_name


def _inject_stdio_auth(config: dict, server_name: str, token: str) -> None:
    """Inject an OAuth/API token into the env dict for a stdio MCP server.

    Each server expects its token in a specific env var. Looks up the var
    name from _STDIO_TOKEN_ENV_VARS by server_name, falling back to
    _infer_provider(). Mutates config["env"] in place — caller must pass
    a copy, not the registered template.
    """
    env_var = _STDIO_TOKEN_ENV_VARS.get(server_name)
    if not env_var:
        provider = _infer_provider(server_name)
        env_var = _STDIO_TOKEN_ENV_VARS.get(provider)

    if not env_var:
        logger.debug(
            "No env var mapping for stdio server %s — auth token not injected",
            server_name,
        )
        return

    env = config.setdefault("env", {})
    env[env_var] = token
