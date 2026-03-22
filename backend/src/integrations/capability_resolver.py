"""Capability Resolver — routes capabilities to the best available backend.

Given a capability like "email.send", resolves to the best backend:
1. Native adapter (highest priority, T0 trust)
2. Official MCP server (T1 trust)
3. User-added MCP server (T2/T3 trust)

Selection considers priority, health, and trust tier.
Uses session pool for external MCP calls (no gateway layer).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.integrations.capabilities import (
    CAPABILITY_CATALOG,
    get_capability_for_tool,
)
from src.models.capability_binding import CapabilityBinding

if TYPE_CHECKING:
    from src.integrations.session_pool import UserMCPSessionPool

logger = logging.getLogger(__name__)


class ResolvedBackend:
    """Result of resolving a capability to a concrete backend."""

    __slots__ = (
        "capability",
        "backend_type",
        "backend_ref",
        "tool_name",
        "trust_id",
        "priority",
    )

    def __init__(
        self,
        capability: str,
        backend_type: str,
        backend_ref: str,
        tool_name: str,
        trust_id: str | None = None,
        priority: int = 0,
    ):
        self.capability = capability
        self.backend_type = backend_type
        self.backend_ref = backend_ref
        self.tool_name = tool_name
        self.trust_id = trust_id
        self.priority = priority


class CapabilityResolver:
    """Resolves capabilities to backends and executes tool calls through them."""

    def __init__(
        self,
        db: AsyncSession,
        session_pool: UserMCPSessionPool | None,
        workspace_id: str,
    ):
        self._db = db
        self._session_pool = session_pool
        self._workspace_id = workspace_id

    async def resolve(self, capability: str) -> ResolvedBackend | None:
        """Find the best backend for a capability.

        Resolution order by priority (highest first):
        1. Native backends (internal handlers)
        2. Official MCP (mcp_official)
        3. User MCP (mcp_user)
        """
        stmt = (
            select(CapabilityBinding)
            .where(
                CapabilityBinding.workspace_id == self._workspace_id,
                CapabilityBinding.capability == capability,
                CapabilityBinding.enabled.is_(True),
            )
            .order_by(CapabilityBinding.priority.desc())
        )
        result = await self._db.execute(stmt)
        bindings = list(result.scalars().all())

        if not bindings:
            return None

        # Pick highest priority binding where the backend is healthy
        for binding in bindings:
            if self._is_backend_healthy(binding):
                return ResolvedBackend(
                    capability=binding.capability,
                    backend_type=binding.backend_type,
                    backend_ref=binding.backend_ref or "",
                    tool_name=binding.tool_name or "",
                    trust_id=binding.trust_id,
                    priority=binding.priority,
                )

        # Fallback to first binding even if health unknown
        b = bindings[0]
        return ResolvedBackend(
            capability=b.capability,
            backend_type=b.backend_type,
            backend_ref=b.backend_ref or "",
            tool_name=b.tool_name or "",
            trust_id=b.trust_id,
            priority=b.priority,
        )

    def resolve_tool_to_capability(self, tool_name: str) -> str | None:
        """Map a tool name to its canonical capability."""
        return get_capability_for_tool(tool_name)

    async def execute(
        self,
        tool_name: str,
        tool_input: dict,
        *,
        user_id: str,
    ) -> dict:
        """Execute a tool call, routing through the appropriate backend.

        Resolution:
        1. Look up capability for the tool
        2. Resolve capability to best backend
        3. Execute via native handler or session pool
        """
        capability = get_capability_for_tool(tool_name)

        if capability:
            backend = await self.resolve(capability)
            if backend and backend.backend_type == "native":
                return await self._execute_native(tool_name, tool_input, user_id=user_id)
            if backend and backend.backend_type in ("mcp_official", "mcp_user"):
                return await self._execute_external(
                    tool_name, tool_input, user_id=user_id,
                    server_name=backend.backend_ref,
                )

        # No capability mapping — try session pool directly
        if self._session_pool and self._session_pool.is_pool_tool(tool_name):
            server_name = self._session_pool.get_server_for_tool(tool_name)
            if server_name:
                return await self._execute_external(
                    tool_name, tool_input, user_id=user_id,
                    server_name=server_name,
                )

        # Fall through to native execution
        return await self._execute_native(tool_name, tool_input, user_id=user_id)

    # Cached in-process client for internal MCP tools
    _cached_client = None
    _cached_client_ctx = None

    async def _execute_native(
        self,
        tool_name: str,
        tool_input: dict,
        *,
        user_id: str,
    ) -> dict:
        """Execute via in-process FastMCP Client (MCP protocol)."""
        import json

        from fastmcp import Client

        from src.tools.server import jarvis_tools

        # Lazy-init cached client
        if CapabilityResolver._cached_client is None:
            CapabilityResolver._cached_client_ctx = Client(jarvis_tools)
            CapabilityResolver._cached_client = (
                await CapabilityResolver._cached_client_ctx.__aenter__()
            )

        # Map flat tool name to namespaced (intelligence_ prefix)
        namespaced = f"intelligence_{tool_name}"
        result = await CapabilityResolver._cached_client.call_tool(
            namespaced, {**tool_input, "user_id": user_id}
        )

        if result.is_error:
            error_text = result.data if hasattr(result, "data") else str(result)
            raise RuntimeError(f"Internal tool '{tool_name}' error: {error_text}")

        # Extract structured content
        if hasattr(result, "structured_content") and result.structured_content:
            return result.structured_content.get("result", result.structured_content)

        text = result.data if hasattr(result, "data") else str(result)
        if isinstance(text, str):
            try:
                return json.loads(text)
            except (json.JSONDecodeError, TypeError):
                return {"status": "ok", "result": text}
        return {"status": "ok", "result": text}

    async def _execute_external(
        self,
        tool_name: str,
        tool_input: dict,
        *,
        user_id: str,
        server_name: str,
    ) -> dict:
        """Execute via MCP session pool (external servers with auth + circuit breaking)."""
        if not self._session_pool:
            raise RuntimeError("MCP session pool not available")

        # Trust enforcement: check write tools on low-trust servers
        self._check_trust(tool_name, server_name)

        return await self._session_pool.call_tool(
            tool_name,
            tool_input,
            user_id=user_id,
            server_name=server_name,
            workspace_id=self._workspace_id,
        )

    def _check_trust(self, tool_name: str, server_name: str) -> None:
        """Check trust enforcement for external tool calls.

        Write tools on untrusted servers log a warning. Full approval gating
        is handled by the Governor pre-hook in the orchestrator.
        """
        capability = get_capability_for_tool(tool_name)
        if capability and capability in CAPABILITY_CATALOG:
            cap_meta = CAPABILITY_CATALOG[capability]
            if not cap_meta.read_only:
                logger.info(
                    "External write tool via MCP: tool=%s server=%s capability=%s",
                    tool_name,
                    server_name,
                    capability,
                )

    def _is_backend_healthy(self, binding: CapabilityBinding) -> bool:
        """Check if a backend is healthy enough to use."""
        if binding.backend_type == "native":
            return True
        if binding.backend_type in ("mcp_official", "mcp_user") and self._session_pool:
            server_name = binding.backend_ref
            if server_name:
                return self._session_pool._circuit_breaker.is_available(server_name)
        return True

    async def get_capability_health(self) -> dict[str, str]:
        """Get health status for each capability family."""
        from src.integrations.capabilities import CapabilityFamily

        family_health: dict[str, str] = {}
        for family in CapabilityFamily:
            family_health[family.value] = "unknown"

        stmt = select(CapabilityBinding).where(
            CapabilityBinding.workspace_id == self._workspace_id,
            CapabilityBinding.enabled.is_(True),
        )
        result = await self._db.execute(stmt)
        bindings = list(result.scalars().all())

        for binding in bindings:
            family = binding.family
            if not family:
                continue
            healthy = self._is_backend_healthy(binding)
            current = family_health.get(family, "unknown")
            if healthy and current != "healthy":
                family_health[family] = "healthy"
            elif not healthy and current == "unknown":
                family_health[family] = "degraded"

        return family_health
