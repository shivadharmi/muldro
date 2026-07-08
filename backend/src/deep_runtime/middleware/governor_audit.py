"""Deep-runtime Governor audit middleware (Step 7B1).

Port of the legacy ``governor_pre_tool_hook`` (``src/orchestrator/hooks.py``) to a
``@wrap_tool_call`` middleware for the deep path: audit-log every Jarvis tool call, BLOCK
disabled tools (→ ``ToolMessage`` error), and fall through for deepagents built-ins. This is
AUDIT-ONLY — approval gating is the trust_gate's job (Step 6B); the block here mirrors the
legacy hook's single safety invariant (a disabled tool never runs).

Placed FIRST in ``extra_middleware`` so the composed chain is
``capability_scope → governor_audit → trust_gate → write_lock → dispatcher``. The tool-def
lookup fails OPEN (audit-only): a transient DB/registry error never blocks a tool, matching
the legacy hook's ``except Exception: pass`` allow-through.

Step 7B1 P2 (6C #1): the registry lookup is no longer done here. governor_audit consumes the
per-turn SHARED ``_resolve_tool_def`` (injected as ``resolve_tool_def``), memoized in the
invoker and shared with trust_gate + write_lock — three consumers, one lookup, one session.
governor_audit derives its OWN projection (``.enabled`` for the block, ``.risk_level`` for the
audit) and keeps its OWN fail-OPEN policy over ``(False, None)``.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware, wrap_tool_call
from langchain_core.messages import ToolMessage

from src.deep_runtime.builtins import DEEPAGENTS_BUILTIN_NAMES

logger = logging.getLogger(__name__)

ResolveToolDefFn = Callable[[str], Awaitable[tuple[bool, Any]]]


def make_governor_audit_middleware(
    *, agent_name: str, workspace_id: str, resolve_tool_def: ResolveToolDefFn
) -> AgentMiddleware:
    """Build the Governor audit middleware for one turn.

    ``agent_name`` / ``workspace_id`` are captured in the closure — never LLM-supplied.

    Args:
        agent_name: The routed sub-agent's name, stamped onto each audit/block log record.
        workspace_id: Tenant scope, stamped onto each audit log record.
        resolve_tool_def: Async ``(name) -> (lookup_ok, tool_def | None)`` — the per-turn
            SHARED ToolDef resolver (6C #1), memoized in the invoker and shared with trust_gate
            + write_lock. It handles its OWN lookup error internally and returns ``(False,
            None)``; governor_audit then falls OPEN (allow, audit low-risk) — an audit hook
            must never turn a transient lookup error into a blocked tool.

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

        # Classify via the SHARED resolver for audit + the disabled-tool block. Fail OPEN: the
        # resolver returns (False, None) on a lookup error and (True, None) for an unknown
        # tool — both leave the tool unblocked and audited at low risk.
        _ok, tool_def = await resolve_tool_def(name)
        is_blocked = bool(tool_def and not tool_def.enabled)
        risk_level = getattr(tool_def, "risk_level", "low") if tool_def else "low"

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
