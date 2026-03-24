"""Per-user MCP session pool — manages authenticated Client instances.

Each (server_name, user_id) pair gets its own Client with the user's
OAuth token injected. Sessions are lazily created and TTL-cleaned.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from fastmcp import Client
from fastmcp.client.auth import BearerAuth

from src.integrations.tool_normalizer import ToolNameNormalizer, get_normalizer
from src.services.mcp_resilience import MCPCircuitBreaker

logger = logging.getLogger(__name__)

# Default idle timeout before a session is cleaned up (30 minutes)
SESSION_TTL_SECONDS = 1800


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
    """Pool of per-user MCP Client instances with auth, normalization, and circuit breaking.

    Usage:
        pool = UserMCPSessionPool(oauth_manager, circuit_breaker, normalizer)
        result = await pool.call_tool(
            "gmail_send", {...}, user_id="usr_1", server_name="google-workspace",
        )
    """

    def __init__(
        self,
        oauth_manager: Any | None = None,
        circuit_breaker: MCPCircuitBreaker | None = None,
        normalizer: ToolNameNormalizer | None = None,
        ttl_seconds: float = SESSION_TTL_SECONDS,
    ) -> None:
        self._oauth_manager = oauth_manager
        self._circuit_breaker = circuit_breaker or MCPCircuitBreaker()
        self._normalizer = normalizer or get_normalizer()
        self._ttl_seconds = ttl_seconds
        # (server_name, user_id) → SessionEntry
        self._sessions: dict[tuple[str, str], SessionEntry] = {}
        self._lock = asyncio.Lock()
        # server_name → config dict (loaded from IntegrationInstallation)
        self._server_configs: dict[str, dict] = {}
        # server_name → tool mapping (canonical → raw)
        self._server_tools: dict[str, dict[str, str]] = {}
        # canonical_name → metadata
        self._tool_metadata: dict[str, dict[str, Any]] = {}

    def register_server_config(self, server_name: str, config: dict) -> None:
        """Register server configuration for later session creation."""
        self._server_configs[server_name] = config

    async def get_or_create_session(
        self,
        server_name: str,
        user_id: str,
        workspace_id: str = "",
    ) -> SessionEntry:
        """Get an existing session or create a new one with auth."""
        key = (server_name, user_id)

        async with self._lock:
            entry = self._sessions.get(key)
            if entry:
                entry.last_used = time.monotonic()
                return entry

            # Create new session
            config = self._server_configs.get(server_name)
            if not config:
                raise RuntimeError(
                    f"No config registered for MCP server '{server_name}'. "
                    f"Call register_server_config() first."
                )

            # Resolve auth
            auth = await self._resolve_auth(server_name, user_id, config)

            # Create Client
            transport = config.get("transport", "stdio")
            if transport in ("sse", "streamable-http"):
                url = config["url"]
                client_ctx = Client(url, auth=auth) if auth else Client(url)
            else:
                # stdio transport — build config dict
                server_cfg = {"mcpServers": {server_name: config}}
                client_ctx = Client(server_cfg)

            # Connect and discover tools
            client = await client_ctx.__aenter__()
            raw_tools = await client.list_tools()
            tool_dicts = [{"name": t.name, "description": t.description or ""} for t in raw_tools]
            tool_mapping = self._normalizer.register_server_tools(server_name, tool_dicts)
            self._server_tools[server_name] = tool_mapping
            for t in raw_tools:
                canonical = self._normalizer.normalize(t.name, server_name)
                input_schema = (
                    getattr(t, "inputSchema", None)
                    or getattr(t, "input_schema", None)
                    or {"type": "object", "properties": {}}
                )
                self._tool_metadata[canonical] = {
                    "name": canonical,
                    "server": server_name,
                    "description": t.description or "",
                    "input_schema": input_schema,
                }

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
            return {
                "status": "error",
                "error_code": "circuit_open",
                "message": f"MCP server '{server_name}' circuit open",
            }

        # Get or create session
        try:
            session = await self.get_or_create_session(
                server_name, user_id, workspace_id,
            )
        except Exception as e:
            return make_error_response(e, tool_name=tool_name)

        # Resolve canonical → raw MCP tool name
        raw_name = session.tools.get(tool_name) or tool_name

        last_error: Exception | None = None
        for attempt in range(max_retries):
            try:
                result = await session.client.call_tool(raw_name, tool_input)
                self._circuit_breaker.record_success(server_name)
                session.last_used = time.monotonic()

                # Extract content from CallToolResult
                if hasattr(result, "content"):
                    text_parts = []
                    for block in result.content:
                        if hasattr(block, "text"):
                            text_parts.append(block.text)
                        elif hasattr(block, "data"):
                            text_parts.append(str(block.data))
                    return {"status": "ok", "result": "\n".join(text_parts)}

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
                    tool_name, attempt + 1, max_retries, error_code, delay,
                )
                await asyncio.sleep(delay)

        logger.warning(
            "MCP tool '%s' on '%s' failed after %d attempts: %s",
            tool_name, server_name, max_retries, last_error,
        )
        return make_error_response(
            last_error or RuntimeError("Unknown error"),
            tool_name=tool_name,
        )

    async def refresh_session(self, server_name: str, user_id: str) -> None:
        """Force reconnect a session (e.g., after OAuth token refresh)."""
        key = (server_name, user_id)

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
        to_remove: list[tuple[str, str]] = []

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

    def is_pool_tool(self, tool_name: str) -> bool:
        """Check if a tool is known to any server in the pool."""
        for server_tools in self._server_tools.values():
            if tool_name in server_tools:
                return True
        return False

    def get_server_for_tool(self, tool_name: str) -> str | None:
        """Find which server provides a canonical tool name."""
        for server_name, tools in self._server_tools.items():
            if tool_name in tools:
                return server_name
        return None

    def get_all_tools(self) -> dict[str, str]:
        """Return all tools across all servers: {canonical_name: server_name}."""
        result: dict[str, str] = {}
        for server_name, tools in self._server_tools.items():
            for canonical in tools:
                result[canonical] = server_name
        return result

    def get_all_tool_metadata(self) -> list[dict[str, Any]]:
        """Return all tool metadata across servers."""
        result: list[dict[str, Any]] = []
        for name, meta in self._tool_metadata.items():
            item = dict(meta)
            item["name"] = name
            result.append(item)
        return result

    def get_health(self) -> dict[str, dict]:
        """Get health status for all servers in the pool."""
        health: dict[str, dict] = {}
        for (server_name, user_id), entry in self._sessions.items():
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
            provider_name = auth_provider if auth_provider != "oauth" else _infer_provider(
                server_name
            )
            try:
                token = await self._oauth_manager.get_valid_token(user_id, provider_name)
                if token:
                    return BearerAuth(token=token)
                logger.warning(
                    "No OAuth token for user=%s provider=%s", user_id, provider_name
                )
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
