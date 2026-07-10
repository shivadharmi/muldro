"""Step 10B Phase 2: ``ShadowToolExecutor`` — the headline SAFETY guard of the
shadow-compare cutover control plane.

The shadow harness runs the NON-authoritative agent runtime (e.g. the deep
runtime under evaluation) alongside the authoritative one, so their decisions
can be diffed, WITHOUT the shadow run ever performing a real external write.

Safety property: a WRITE-capability tool call is HARD-SUPPRESSED — it returns
a synthetic result and NEVER reaches the real executor's ``execute_tool``.
READ-capability tool calls pass straight through to the real executor.

Fail-closed: classification depends entirely on capability resolution. If the
capability is unknown (``None``) or resolution itself fails to identify it as
read-only, the tool call is treated as a WRITE and suppressed. This is
deliberately STRICTER than ``src.deep_runtime.middleware.write_lock``, which
treats an unknown capability as read-only (safe there only because it merely
skips a *lock*, not because it skips execution). A shadow run must never let
an unclassified tool reach real dispatch.

This module never touches ``src/orchestrator/tool_executor.py`` — the real
executor is injected and used strictly as a passthrough target for reads.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Protocol

from src.integrations.capabilities import is_read_only_capability

ResolveCapabilityFn = Callable[[str], Awaitable[str | None]]


class RealToolExecutor(Protocol):
    """Structural contract for the real executor this class wraps — mirrors
    ``src.orchestrator.tool_executor.ToolExecutor.execute_tool``."""

    async def execute_tool(
        self, tool_name: str, tool_input: dict, user_id: str, workspace_id: str = ""
    ) -> dict: ...


class ShadowToolExecutor:
    """Drop-in ``ExecuteToolFn``-shaped wrapper that suppresses every write.

    ``real_executor`` is the real ``ToolExecutor`` (or anything matching its
    ``execute_tool`` signature) that reads are passed through to.
    ``resolve_capability`` is an async ``tool_name -> capability | None``
    lookup — the same resolution contract used by the write-lock middleware
    (``src/deep_runtime/middleware/write_lock.py``'s ``ResolveCapabilityFn``).
    """

    def __init__(
        self,
        real_executor: RealToolExecutor,
        resolve_capability: ResolveCapabilityFn,
    ) -> None:
        self._real = real_executor
        self._resolve_capability = resolve_capability

    async def execute_tool(
        self, tool_name: str, tool_input: dict, user_id: str, workspace_id: str = ""
    ) -> dict:
        capability = await self._resolve_capability(tool_name)
        is_read = bool(capability) and is_read_only_capability(capability)

        if is_read:
            return await self._real.execute_tool(tool_name, tool_input, user_id, workspace_id)

        # WRITE or UNKNOWN capability → hard-suppress. Never call self._real.
        # No "error" key and no failing "status" — this must read as a
        # *successful* tool result so agent-loop is_error detection doesn't
        # flip it to "tool failed" and trigger a retry.
        return {
            "shadow_suppressed": True,
            "tool": tool_name,
            "capability": capability,
            "note": (
                "Suppressed in shadow/observation mode — the write was NOT performed; do not retry."
            ),
        }
