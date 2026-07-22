"""Central Jarvis tool-execution dispatcher for the deep runtime (Step 6A.5).

Mirrors the legacy agent_loop's "tools are schemas, execution is central" model on the deep
path. Jarvis tools are registered with create_deep_agent as inert schema shells
(tool_bridge.build_tool_shells); this ONE wrap_tool_call middleware intercepts every tool
call and, for a Jarvis tool, dispatches through ToolExecutor.execute_tool WITHOUT invoking
the shell body, then normalizes {"error"|"blocked"} results to ToolMessage(status="error")
so the frozen blocked<-status=="error" SSE mapping holds. It falls through to the real
handler for deepagents' own built-in tools (they must run their own bodies). Capability-scope
enforcement stays a SEPARATE outer middleware — security is not merged into dispatch.

Do NOT wrap execute_tool in a blanket try/except: a future 6B interrupt path must be able to
raise GraphInterrupt through this layer.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable

from langchain.agents.middleware import AgentMiddleware, wrap_tool_call
from langchain_core.messages import ToolMessage

from src.deep_runtime.builtins import DEEPAGENTS_BUILTIN_NAMES

logger = logging.getLogger(__name__)

ExecuteToolFn = Callable[[str, dict, str, str], Awaitable[dict]]


def make_jarvis_tool_dispatcher(
    *,
    execute_tool: ExecuteToolFn,
    user_id: str,
    workspace_id: str,
) -> AgentMiddleware:
    """Build the central tool-execution dispatcher for one turn.

    ``user_id``/``workspace_id`` are captured in the closure — never LLM-supplied.

    Args:
        execute_tool: Async callable matching ``ToolExecutor.execute_tool``'s signature
            ``(name, args, user_id, workspace_id) -> dict``.
        user_id: Authenticated user ID for this turn; injected, never LLM-supplied.
        workspace_id: Tenant workspace ID for this turn; injected, never LLM-supplied.

    Returns:
        An ``AgentMiddleware`` that dispatches Jarvis tool calls through ``execute_tool``
        and falls through for deepagents built-ins.
    """

    @wrap_tool_call
    async def jarvis_tool_dispatcher(request, handler):
        name = request.tool_call["name"]

        if name in DEEPAGENTS_BUILTIN_NAMES:
            # deepagents' own scaffolding tool (write_todos, ls, …) — run its real body.
            logger.debug("[deep_runtime] built-in fall-through: %s", name)
            return await handler(request)

        args = request.tool_call.get("args") or {}
        logger.debug("[deep_runtime] dispatching Jarvis tool: %s args=%r", name, args)

        # Do NOT wrap in try/except — 6B's GraphInterrupt must propagate through.
        result = await execute_tool(name, args, user_id, workspace_id)

        blocked = isinstance(result, dict) and bool(result.get("error") or result.get("blocked"))
        content = json.dumps(result) if isinstance(result, (dict, list)) else str(result)

        return ToolMessage(
            content=content,
            tool_call_id=request.tool_call["id"],
            name=name,
            status="error" if blocked else "success",
        )

    return jarvis_tool_dispatcher
