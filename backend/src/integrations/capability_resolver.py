"""Capability Resolver — routes capabilities to the best available backend.

Given a capability like "email.send", resolves to the best backend:
1. Native adapter (highest priority, T0 trust)
2. Official MCP server (T1 trust)
3. User-added MCP server (T2/T3 trust)

Selection considers priority, health, and trust tier.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.integrations.capabilities import (
    get_capability_for_tool,
)
from src.integrations.gateway import MCPGateway
from src.models.capability_binding import CapabilityBinding

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
        gateway: MCPGateway | None,
        workspace_id: str,
    ):
        self._db = db
        self._gateway = gateway
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
        3. Execute via native handler or gateway
        """
        capability = get_capability_for_tool(tool_name)

        if capability:
            backend = await self.resolve(capability)
            if backend and backend.backend_type == "native":
                return await self._execute_native(tool_name, tool_input, user_id=user_id)
            if backend and backend.backend_type in ("mcp_official", "mcp_user"):
                return await self._execute_via_gateway(tool_name, tool_input)

        # No capability mapping or no binding — try gateway directly
        if self._gateway and self._gateway.is_gateway_tool(tool_name):
            return await self._execute_via_gateway(tool_name, tool_input)

        # Fall through to native execution
        return await self._execute_native(tool_name, tool_input, user_id=user_id)

    async def _execute_native(
        self,
        tool_name: str,
        tool_input: dict,
        *,
        user_id: str,
    ) -> dict:
        """Execute via internal intelligence handler."""
        from src.tools import intelligence_server

        internal_handlers = {
            "ingest_event": intelligence_server.ingest_event,
            "search_memory": intelligence_server.search_memory,
            "get_entities": intelligence_server.get_entities,
            "update_entity": intelligence_server.update_entity,
            "plan_command": intelligence_server.plan_command,
            "get_active_plans": intelligence_server.get_active_plans,
            "evaluate_policy": intelligence_server.evaluate_policy,
            "approve_action": intelligence_server.approve_action,
            "get_briefing": intelligence_server.get_briefing,
            "get_observation_cursor": intelligence_server.get_observation_cursor,
            "update_observation_cursor": intelligence_server.update_observation_cursor,
            "report_observation": intelligence_server.report_observation,
            "update_execution": intelligence_server.update_execution,
            "extract_preferences": intelligence_server.extract_preferences,
            "create_task": intelligence_server.create_task,
            "get_task": intelligence_server.get_task,
            "get_goals": intelligence_server.get_goals,
            "build_context": intelligence_server.build_context,
            "verify_run": intelligence_server.verify_run,
        }

        handler = internal_handlers.get(tool_name)
        if handler:
            return await handler(user_id=user_id, **tool_input)

        raise RuntimeError(f"No native handler for tool '{tool_name}'")

    async def _execute_via_gateway(self, tool_name: str, tool_input: dict) -> dict:
        """Execute via MCP gateway."""
        if not self._gateway:
            raise RuntimeError("MCP gateway not available")
        return await self._gateway.call_tool(tool_name, tool_input)

    def _is_backend_healthy(self, binding: CapabilityBinding) -> bool:
        """Check if a backend is healthy enough to use."""
        if binding.backend_type == "native":
            return True
        if binding.backend_type in ("mcp_official", "mcp_user") and self._gateway:
            server_name = binding.backend_ref
            if server_name:
                health = self._gateway.get_server_health()
                server_health = health.get(server_name, {})
                circuit = server_health.get("circuit_state", "closed")
                return circuit != "open"
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
