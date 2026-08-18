"""Deep-runtime write-lock middleware (Step 6C).

Placed BETWEEN trust_gate (OUTER) and muldro_tool_dispatcher (INNER):
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
from src.integrations.capabilities import SYSTEM_ACTION_CAPABILITIES, is_read_only_capability
from src.services.contention import (
    CONTENDED_MESSAGE,
    WRITE_LOCK_UNAVAILABLE_MESSAGE,
    blocked_body,
)
from src.services.write_lock import WriteLockContended, acquire_write_lock

logger = logging.getLogger(__name__)

ResolveCapabilityFn = Callable[[str], Awaitable[str | None]]


def _blocked_tool_message(error: str, *, tool_call_id: str, name: str) -> ToolMessage:
    """Deep-only envelope: wrap the canonical blocked body (``src.services.contention``) in a
    ``ToolMessage``. Local because only the deep path produces ToolMessages — the autonomous
    path returns the bare dict directly."""
    return ToolMessage(
        content=json.dumps(blocked_body(error)),
        tool_call_id=tool_call_id,
        name=name,
        status="error",
    )


def make_write_lock_middleware(
    *,
    workspace_id: str,
    redis,
    resolve_capability: ResolveCapabilityFn,
    require_redis: bool = False,
) -> AgentMiddleware:
    """Build the per-turn write-lock middleware. ``workspace_id`` is closure-captured
    (never LLM-supplied). ``resolve_capability(name) -> capability|None`` maps a tool name
    to its capability via the registry (same resolution the autonomous path uses).

    ``require_redis`` (Step-10A A3, default False): when True, a WRITE is REFUSED
    (fail-closed) rather than executed unlocked if Redis is unavailable. Default False
    preserves today's fail-OPEN behavior byte-for-byte — the ``redis is None`` early
    return below runs BEFORE capability resolution, so nothing about the flag-off path
    changes (not even an extra ``resolve_capability`` call).
    """

    @wrap_tool_call
    async def write_lock(request, handler):
        name = request.tool_call["name"]
        if name in DEEPAGENTS_BUILTIN_NAMES:
            return await handler(request)
        if redis is None and not require_redis:
            # No Redis wired + fail-open (default): fall through exactly as before.
            return await handler(request)

        capability = await resolve_capability(name)
        # system.* internal action tools are the user's own memory (reversible, `self`
        # blast-radius) and never contend cross-path — the autonomous handler never locks
        # them either. ALWAYS-ALLOWED (D5): exempt from the write lock entirely, including the
        # require_redis fail-closed branch below. Matched against the EXPLICIT
        # SYSTEM_ACTION_CAPABILITIES set (not a `system.` prefix) so a future system.* capability
        # is locked by default until deliberately exempted.
        if capability in SYSTEM_ACTION_CAPABILITIES:
            return await handler(request)
        if redis is None:
            # require_redis is True here: Redis expected up but down. Refuse WRITES
            # (fail-closed) rather than execute unlocked; reads still pass.
            if not is_read_only_capability(capability):
                logger.warning(
                    "[deep_runtime] write refused (redis required, unavailable): %s", name
                )
                return _blocked_tool_message(
                    WRITE_LOCK_UNAVAILABLE_MESSAGE,
                    tool_call_id=request.tool_call["id"],
                    name=name,
                )
            return await handler(request)
        if not capability or is_read_only_capability(capability):
            return await handler(request)  # reads never lock

        try:
            async with acquire_write_lock(redis, workspace_id, capability):
                return await handler(request)
        except WriteLockContended:
            logger.warning("[deep_runtime] write lock contended for %s (%s)", name, capability)
            return _blocked_tool_message(
                CONTENDED_MESSAGE, tool_call_id=request.tool_call["id"], name=name
            )

    return write_lock
