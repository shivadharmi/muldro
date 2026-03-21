"""MCP Gateway — managed fastmcp.Client pool with circuit breaker.

Replaces direct mcp_bridge usage with a workspace-aware, health-tracked
gateway that manages per-server client instances. Features:
- Per-server circuit breaker (open after N consecutive failures)
- Manifest normalization (tool name dedup)
- Audit logging for cross-boundary calls
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import StrEnum

from src.integrations.capabilities import CAPABILITY_CATALOG, get_capability_for_tool
from src.integrations.trust_enforcer import TrustEnforcer

logger = logging.getLogger(__name__)

# Circuit breaker defaults
CB_FAILURE_THRESHOLD = 3
CB_RECOVERY_SECONDS = 60.0


class CircuitState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitBreaker:
    """Per-server circuit breaker."""

    failure_count: int = 0
    state: CircuitState = CircuitState.CLOSED
    last_failure_time: float = 0.0

    def record_success(self) -> None:
        self.failure_count = 0
        self.state = CircuitState.CLOSED

    def record_failure(self) -> None:
        self.failure_count += 1
        self.last_failure_time = time.monotonic()
        if self.failure_count >= CB_FAILURE_THRESHOLD:
            self.state = CircuitState.OPEN

    def is_available(self) -> bool:
        if self.state == CircuitState.CLOSED:
            return True
        if self.state == CircuitState.OPEN:
            elapsed = time.monotonic() - self.last_failure_time
            if elapsed >= CB_RECOVERY_SECONDS:
                self.state = CircuitState.HALF_OPEN
                return True
            return False
        # half_open — allow one probe
        return True


@dataclass
class ServerConnection:
    """Tracked connection to a single MCP server."""

    server_name: str
    tools: list[dict] = field(default_factory=list)
    circuit: CircuitBreaker = field(default_factory=CircuitBreaker)
    connected: bool = False
    last_call_ms: int = 0


class MCPGateway:
    """Manages MCP server connections for a workspace.

    Uses the mcp_bridge module for actual transport — this layer adds
    circuit breaking, health tracking, and audit logging on top.
    """

    def __init__(self) -> None:
        self._servers: dict[str, ServerConnection] = {}
        self._tool_server_map: dict[str, str] = {}  # tool_name -> server_name
        self._server_tiers: dict[str, str] = {}  # server_name -> trust_tier
        self._trust_enforcer = TrustEnforcer()
        self._lock = asyncio.Lock()

    # ── Initialization ──────────────────────────────────────────────

    async def initialize(self, mcp_configs: dict) -> None:
        """Initialize gateway from MCP server configurations.

        Args:
            mcp_configs: The mcpServers dict from control plane.
        """
        servers_cfg = mcp_configs.get("mcpServers", {})
        if not servers_cfg:
            return

        from src.connectors.mcp_bridge import list_mcp_tools

        # Register all known MCP tools into the gateway
        all_tools = list_mcp_tools()
        for tool in all_tools:
            tool_name = tool["name"]
            # Infer server name from tool prefix
            server_name = self._infer_server(tool_name, servers_cfg)
            if server_name:
                if server_name not in self._servers:
                    self._servers[server_name] = ServerConnection(
                        server_name=server_name, connected=True
                    )
                self._servers[server_name].tools.append(tool)
                self._tool_server_map[tool_name] = server_name

        logger.info(
            "MCPGateway initialized: %d servers, %d tools",
            len(self._servers),
            len(self._tool_server_map),
        )

    def _infer_server(self, tool_name: str, servers_cfg: dict) -> str | None:
        """Infer which server a tool belongs to from its name prefix."""
        for server_name in servers_cfg:
            normalized = server_name.replace("-", "_")
            if tool_name.startswith(f"{normalized}_"):
                return server_name
        # Fallback: check if the tool is a known raw name
        return None

    # ── Tool execution ──────────────────────────────────────────────

    def set_server_tier(self, server_name: str, trust_tier: str) -> None:
        """Set the trust tier for a server (called during initialization)."""
        self._server_tiers[server_name] = trust_tier

    def get_server_tier(self, server_name: str) -> str:
        """Get the trust tier for a server. Defaults to T3 if unknown."""
        return self._server_tiers.get(server_name, "T3")

    async def call_tool(self, tool_name: str, tool_input: dict) -> dict:
        """Execute an MCP tool with circuit breaker and trust enforcement.

        Raises RuntimeError if circuit is open, tool not found, or trust enforcement blocks.
        """
        server_name = self._tool_server_map.get(tool_name)
        if not server_name:
            raise RuntimeError(f"Tool '{tool_name}' not registered in gateway")

        conn = self._servers[server_name]
        if not conn.circuit.is_available():
            raise RuntimeError(
                f"Circuit open for server '{server_name}': "
                f"{conn.circuit.failure_count} consecutive failures"
            )

        # Trust enforcement
        trust_tier = self.get_server_tier(server_name)
        is_write = self._is_write_tool(tool_name)
        enforcement = self._trust_enforcer.check(server_name, trust_tier, tool_name, is_write)
        if not enforcement.allowed:
            raise RuntimeError(f"Trust enforcement blocked: {enforcement.reason}")
        if enforcement.requires_approval:
            logger.info(
                "trust_approval_required",
                extra={"tool": tool_name, "server": server_name, "tier": trust_tier},
            )

        self._trust_enforcer.record_call(server_name)
        t0 = time.monotonic()
        try:
            from src.connectors.mcp_bridge import call_mcp_tool

            result = await call_mcp_tool(tool_name, tool_input)
            conn.circuit.record_success()
            conn.last_call_ms = int((time.monotonic() - t0) * 1000)

            logger.debug(
                "gateway_tool_call",
                extra={
                    "tool": tool_name,
                    "server": server_name,
                    "tier": trust_tier,
                    "latency_ms": conn.last_call_ms,
                },
            )
            return result

        except Exception as e:
            conn.circuit.record_failure()
            conn.last_call_ms = int((time.monotonic() - t0) * 1000)
            logger.warning(
                "gateway_tool_failed",
                extra={
                    "tool": tool_name,
                    "server": server_name,
                    "error": str(e)[:200],
                    "circuit_state": conn.circuit.state,
                },
            )
            raise
        finally:
            self._trust_enforcer.complete_call(server_name)

    # ── Query ───────────────────────────────────────────────────────

    def is_gateway_tool(self, tool_name: str) -> bool:
        """Check if a tool is managed by this gateway."""
        return tool_name in self._tool_server_map

    def get_server_for_tool(self, tool_name: str) -> str | None:
        """Get the server name a tool belongs to."""
        return self._tool_server_map.get(tool_name)

    def get_server_health(self) -> dict[str, dict]:
        """Get health status for all servers."""
        result: dict[str, dict] = {}
        for name, conn in self._servers.items():
            result[name] = {
                "connected": conn.connected,
                "circuit_state": conn.circuit.state,
                "failure_count": conn.circuit.failure_count,
                "tool_count": len(conn.tools),
                "last_call_ms": conn.last_call_ms,
            }
        return result

    def list_tools(self) -> list[dict]:
        """List all tools across all connected servers."""
        tools = []
        for conn in self._servers.values():
            tools.extend(conn.tools)
        return tools

    def get_tools_for_server(self, server_name: str) -> list[dict]:
        """Get tools for a specific server."""
        conn = self._servers.get(server_name)
        return conn.tools if conn else []

    def normalize_tool_name(self, tool_name: str) -> str:
        """Normalize a tool name by resolving to its canonical capability."""
        cap = get_capability_for_tool(tool_name)
        return cap if cap else tool_name

    def _is_write_tool(self, tool_name: str) -> bool:
        """Determine if a tool performs write operations."""
        cap = get_capability_for_tool(tool_name)
        if cap and cap in CAPABILITY_CATALOG:
            return not CAPABILITY_CATALOG[cap].read_only
        write_indicators = ["create", "update", "delete", "send", "write", "post", "put", "remove"]
        name_lower = tool_name.lower()
        return any(ind in name_lower for ind in write_indicators)

    def get_trust_enforcer(self) -> TrustEnforcer:
        """Access the trust enforcer for external usage queries."""
        return self._trust_enforcer
