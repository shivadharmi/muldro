"""Deep-runtime Governor audit middleware (Step 7B1).

Port of the legacy ``governor_pre_tool_hook`` (``src/orchestrator/hooks.py``) to a
``@wrap_tool_call`` middleware for the deep path: audit-log every Jarvis tool call, BLOCK
disabled tools (→ ``ToolMessage`` error), and fall through for deepagents built-ins. This is
AUDIT-ONLY — approval gating is the trust_gate's job (Step 6B); the block here mirrors the
legacy hook's single safety invariant (a disabled tool never runs).

Placed FIRST in ``extra_middleware`` so the composed chain is
``capability_scope → governor_audit → trust_gate → write_lock → dispatcher``. The registry
lookup fails OPEN (audit-only): a transient DB/registry error never blocks a tool, matching
the legacy hook's ``except Exception: pass`` allow-through.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware, wrap_tool_call
from langchain_core.messages import ToolMessage

from src.deep_runtime.builtins import DEEPAGENTS_BUILTIN_NAMES
from src.services.tool_registry import ToolRegistry

logger = logging.getLogger(__name__)


def make_governor_audit_middleware(
    *, agent_name: str, workspace_id: str, db_factory: Callable[[], Any]
) -> AgentMiddleware:
    """Build the Governor audit middleware for one turn.

    ``agent_name`` / ``workspace_id`` are captured in the closure — never LLM-supplied.

    Args:
        agent_name: The routed sub-agent's name, stamped onto each audit/block log record.
        workspace_id: Tenant scope for the registry lookup (``"" -> None``).
        db_factory: Async-context-manager factory yielding an ``AsyncSession``. Each call opens
            and closes a short-lived session for the registry lookup.

    Returns:
        An ``AgentMiddleware`` exposing an async ``wrap_tool_call`` hook.
    """

    @wrap_tool_call
    async def governor_audit(request, handler):
        name = request.tool_call["name"]

        # deepagents built-ins (write_todos, ls, task, …) are framework scaffolding — never
        # a Jarvis registry tool, so skip the lookup and let them run their own bodies.
        if name in DEEPAGENTS_BUILTIN_NAMES:
            return await handler(request)

        # Classify via the registry for audit + the disabled-tool block. Fail OPEN: an audit
        # hook must never turn a transient lookup error into a blocked tool.
        is_blocked = False
        risk_level = "low"
        try:
            async with db_factory() as db:
                tool_def = await ToolRegistry(db, workspace_id or None).get_tool(name)
                if tool_def:
                    is_blocked = not tool_def.enabled
                    risk_level = tool_def.risk_level
        except Exception:
            logger.debug("[deep_runtime] governor_audit lookup failed for %s", name, exc_info=True)

        # Blocked tools never run (the one safety invariant carried over from the legacy hook).
        if is_blocked:
            logger.warning("governor_blocked_tool", extra={"tool": name, "agent": agent_name})
            return ToolMessage(
                content=json.dumps(
                    {"error": f"Tool '{name}' is blocked by policy", "blocked": True}
                ),
                tool_call_id=request.tool_call["id"],
                name=name,
                status="error",
            )

        # Audit log — every non-blocked Jarvis tool call.
        logger.info(
            "tool_audit",
            extra={
                "tool": name,
                "agent": agent_name,
                "risk_level": risk_level,
                "workspace_id": workspace_id,
            },
        )
        return await handler(request)

    return governor_audit
