"""Deep-runtime write-lock middleware (Step 6C).

Placed BETWEEN trust_gate (OUTER) and jarvis_tool_dispatcher (INNER):
    capability_scope → trust_gate → write_lock → dispatcher
So it runs AFTER approval (trust_gate calls handler only post-approve, or immediately on the
dormant direct path) and IMMEDIATELY BEFORE execute_tool — never across the interrupt wait.
Reads and built-ins pass straight through. The lock key is shared with the autonomous path
(src.services.write_lock) so a chat write and a scheduler write to the same capability
mutually exclude.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable

from langchain.agents.middleware import AgentMiddleware, wrap_tool_call
from langchain_core.messages import ToolMessage

from src.deep_runtime.builtins import DEEPAGENTS_BUILTIN_NAMES
from src.integrations.capabilities import is_read_only_capability
from src.services.write_lock import WriteLockContended, acquire_write_lock

logger = logging.getLogger(__name__)

ResolveCapabilityFn = Callable[[str], Awaitable[str | None]]


def make_write_lock_middleware(
    *,
    workspace_id: str,
    redis,
    resolve_capability: ResolveCapabilityFn,
) -> AgentMiddleware:
    """Build the per-turn write-lock middleware. ``workspace_id`` is closure-captured
    (never LLM-supplied). ``resolve_capability(name) -> capability|None`` maps a tool name
    to its capability via the registry (same resolution the autonomous path uses)."""

    @wrap_tool_call
    async def write_lock(request, handler):
        name = request.tool_call["name"]
        if name in DEEPAGENTS_BUILTIN_NAMES:
            return await handler(request)
        if redis is None:
            # No Redis wired — fall through (the lock is a safety fence, not a hard dep on
            # the dormant/legacy path). Logged once so degradation is visible.
            return await handler(request)

        capability = await resolve_capability(name)
        if not capability or is_read_only_capability(capability):
            return await handler(request)  # reads never lock

        try:
            async with acquire_write_lock(redis, workspace_id, capability):
                return await handler(request)
        except WriteLockContended:
            logger.warning("[deep_runtime] write lock contended for %s (%s)", name, capability)
            return ToolMessage(
                content=json.dumps(
                    {
                        "error": "resource busy — another write is in progress, retry",
                        "blocked": True,
                    }
                ),
                tool_call_id=request.tool_call["id"],
                name=name,
                status="error",
            )

    return write_lock
