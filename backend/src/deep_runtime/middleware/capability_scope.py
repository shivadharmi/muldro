"""Capability-scope enforcement middleware for deepagents tool calls.

CRITICAL-SECURITY. This is the only safety net on the *ungated* chat path of the
Jarvis → Deep Agents runtime: even when no TrustEngine approval gate fires, an
agent must never be able to call a tool whose capability lies outside its
``capability_scope``. This ports the legacy in-loop check from
``src/orchestrator/agent_loop.py`` (``_capability_in_scope`` /
``_resolve_tool_scope_and_server`` and its denial shape ≈697-742) into a
deepagents ``wrap_tool_call`` interceptor.

Enforcement is **fail-closed**: a tool is allowed *only* when a single
``ToolRegistry.get_tool`` lookup resolves a capability that is present in the
agent's scope. Every other outcome — empty scope, no ``db_factory``, unknown
tool (``None``), or ``tool.capability is None`` — denies the call. On denial the
interceptor short-circuits: it returns a ``ToolMessage`` carrying the legacy
error JSON with ``status="error"`` and does **not** invoke the downstream
handler, so the tool never executes.
"""

from __future__ import annotations

import json
import logging

from langchain.agents.middleware import AgentMiddleware, wrap_tool_call
from langchain_core.messages import ToolMessage

from src.deep_runtime.builtins import DEEPAGENTS_BUILTIN_NAMES
from src.orchestrator.agents import SubAgent
from src.services.tool_registry import ToolRegistry

logger = logging.getLogger(__name__)


async def _is_in_scope(
    tool_name: str,
    agent: SubAgent,
    workspace_id: str,
    db_factory,
) -> bool:
    """Fail-closed capability-scope verdict via ONE registry lookup.

    Mirrors ``agent_loop._resolve_tool_scope_and_server``: allow iff the tool's
    registry capability is non-empty and present in the agent's
    ``capability_scope``. Deny on empty scope, missing ``db_factory``, unknown
    tool, or ``capability is None``.
    """
    scope = getattr(agent, "capability_scope", None)
    # Agents with no scope are offered no tools; nothing legitimate to allow.
    if not scope:
        return False
    if db_factory is None:
        return False
    try:
        async with db_factory() as db:
            registry = ToolRegistry(db, workspace_id=workspace_id or None)
            tool = await registry.get_tool(tool_name)
    except Exception:
        logger.warning(
            "[deep_runtime] %s DENIED %s — capability lookup failed (fail-closed)",
            agent.name,
            tool_name,
        )
        return False
    if tool is None:
        return False
    capability = getattr(tool, "capability", None)
    if not capability:
        return False
    return capability in scope


def make_capability_scope_middleware(
    *,
    agent: SubAgent,
    workspace_id: str,
    db_factory,
) -> AgentMiddleware:
    """Build a capability-scope enforcement middleware for *agent*.

    The returned middleware wraps every tool call: it resolves the called tool's
    capability against ``agent.capability_scope`` (one ``ToolRegistry`` lookup,
    fail-closed) and blocks the call when the capability is out of scope.

    Args:
        agent: The Jarvis sub-agent whose ``capability_scope`` gates tool use.
        workspace_id: Tenant scope for the registry lookup (``"" -> None``).
        db_factory: Async-context-manager factory yielding an ``AsyncSession``.

    Returns:
        An ``AgentMiddleware`` exposing an async ``wrap_tool_call`` hook.
    """

    @wrap_tool_call
    async def capability_scope_guard(request, handler):
        tool_name = request.tool_call["name"]
        # deepagents built-ins (write_todos, ls, read_file, …) are auto-installed
        # framework scaffolding that Jarvis cannot drop.  They are NOT Jarvis registry
        # tools, so skip the capability lookup and let them run their own bodies.
        if tool_name in DEEPAGENTS_BUILTIN_NAMES:
            return await handler(request)
        if await _is_in_scope(tool_name, agent, workspace_id, db_factory):
            return await handler(request)

        error = {
            "error": (
                f"Agent '{agent.name}' is not permitted to call "
                f"'{tool_name}' — capability is outside its scope."
            ),
        }
        logger.warning(
            "[deep_runtime] %s DENIED %s — out of capability scope",
            agent.name,
            tool_name,
        )
        return ToolMessage(
            content=json.dumps(error),
            tool_call_id=request.tool_call["id"],
            status="error",
        )

    return capability_scope_guard
