"""Suppress the deepagents built-ins that nothing in Muldro backs.

``create_deep_agent`` auto-installs a scaffolding toolset Muldro cannot drop:
``FilesystemMiddleware`` and ``SubAgentMiddleware`` are in deepagents'
``_REQUIRED_MIDDLEWARE`` and ``_apply_excluded_middleware`` raises rather than strip them.
So the filesystem tools were offered to every agent regardless of its capability scope —
including a chat lead whose plan says "respond" and whose Muldro scope is EMPTY.

The filesystem is deepagents' per-thread virtual state, so this was never a sandbox
escape. It is still an affordance that silently discards data: Muldro has no filesystem
feature, so a model asked to remember something can "save" it there, report success, and
lose it at end of thread. That is soul law 5 broken by a tool we never meant to offer.

We cannot remove the tools, so we stop them being OFFERED — the same technique deepagents
uses internally for harness profiles (``_ToolExclusionMiddleware``): a ``wrap_model_call``
that filters ``request.tools`` before the model sees them. Every gate keeps its existing
built-in exemption; nothing about how a call would be HANDLED changes, because with the
tool unoffered there is no call.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import wrap_model_call

from src.deep_runtime.builtins import DELEGATION_TOOL_NAME, VIRTUAL_FILESYSTEM_TOOL_NAMES

logger = logging.getLogger(__name__)


def _tool_name(tool: Any) -> str | None:  # noqa: ANN401
    if isinstance(tool, dict):
        name = tool.get("name")
        return name if isinstance(name, str) else None
    name = getattr(tool, "name", None)
    return name if isinstance(name, str) else None


def make_unbacked_builtin_suppressor(*, has_delegates: bool = False):
    """Build the middleware that drops unbacked built-ins from a model request.

    Args:
        has_delegates: whether a Muldro delegate is registered on this agent. When False,
            ``task`` is suppressed too: it would reach deepagents' auto-added
            general-purpose subagent rather than a Muldro one. When True it is kept —
            it is the only route to the delegate, and ``governor_delegate_critique``
            gates it by name.
    """
    suppressed = set(VIRTUAL_FILESYSTEM_TOOL_NAMES)
    if not has_delegates:
        suppressed.add(DELEGATION_TOOL_NAME)

    @wrap_model_call(name="unbacked_builtins")
    async def _suppress(
        request: Any,  # noqa: ANN401
        handler: Callable[[Any], Awaitable[Any]],
    ) -> Any:  # noqa: ANN401
        tools = getattr(request, "tools", None)
        if tools:
            kept = [t for t in tools if _tool_name(t) not in suppressed]
            if len(kept) != len(tools):
                request = request.override(tools=kept)
        return await handler(request)

    return _suppress
