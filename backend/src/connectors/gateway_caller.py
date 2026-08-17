"""The single seam between a perception connector and gateway MCP transport.

A connector holds OpenConnector **actionIds** (dotted, the registry's vocabulary)
and knows nothing about tool names, tenants, JWTs, or sessions. This caller binds
the identity the poller already resolved and performs the one legal name mapping
(``.`` -> ``_``, required because LLM tool-name regexes forbid dots).

Keeping transport here — rather than importing ``call_mcp_tool`` into each
connector — means the MCP-error classification lives in exactly one place and
unit tests substitute a fake caller instead of patching a module global.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.connectors.mcp_bridge import call_mcp_tool
from src.integrations.gateway_naming import action_id_to_tool_name


@dataclass(frozen=True)
class GatewayToolCaller:
    """Invokes gateway actions as the bound principal.

    ``user_id``/``workspace_id`` are bound at construction by the poller, which
    is the layer that already knows them. They are never read from a payload.
    """

    user_id: str
    workspace_id: str

    async def call(self, action_id: str, payload: dict) -> dict:
        """Execute one gateway action; returns the raw MCP result envelope.

        Raises ``ValueError`` for an actionId that cannot become a legal tool
        name — a registration-time bug, surfaced before any transport work.
        """
        tool_name = action_id_to_tool_name(action_id)
        return await call_mcp_tool(
            tool_name,
            payload,
            user_id=self.user_id,
            workspace_id=self.workspace_id,
        )
