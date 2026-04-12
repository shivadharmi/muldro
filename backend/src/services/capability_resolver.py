"""Capability resolver: maps capability strings to tool definitions and agents."""

from __future__ import annotations

import logging

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.tool_definitions import ToolDefinition

logger = logging.getLogger(__name__)


class CapabilityResolver:
    """Maps capability strings (e.g. ``"email.search"``) to concrete tool definitions.

    This is the bridge between Level 2 (capabilities) and Level 3 (tools) in the
    capability infrastructure.
    """

    def __init__(self, db: AsyncSession, workspace_id: str = "") -> None:
        self._db = db
        self._workspace_id = workspace_id

    async def _list_enabled_tools(self) -> list[ToolDefinition]:
        """Return enabled tool definitions visible to the current workspace.

        Includes global tools (``workspace_id IS NULL``) and tools scoped
        explicitly to ``self._workspace_id``.  This prevents cross-tenant
        tool leakage when multiple workspaces have workspace-specific tools.
        """
        stmt = select(ToolDefinition).where(
            ToolDefinition.enabled.is_(True),
            or_(
                ToolDefinition.workspace_id.is_(None),
                ToolDefinition.workspace_id == self._workspace_id,
            ),
        )
        result = await self._db.execute(stmt)
        return list(result.scalars().all())

    async def resolve(self, capability: str) -> list[ToolDefinition]:
        """Find all enabled tools that match the given capability exactly."""
        tools = await self._list_enabled_tools()
        return [t for t in tools if t.capability == capability]

    async def resolve_for_step(self, step_capability: str) -> list[dict]:
        """Return Claude API-format tool dicts for a plan step.

        Includes:
        - Primary tools matching ``step_capability``
        - Related read-only tools from the same capability family (same prefix
          before ``"."``, ``requires_approval=False``, different capability)

        Returns a deduplicated list of ``{"name", "description", "input_schema"}``
        dicts.
        """
        all_tools = await self._list_enabled_tools()
        family = step_capability.split(".")[0] if "." in step_capability else step_capability

        primary = [t for t in all_tools if t.capability == step_capability]
        related_read = [
            t
            for t in all_tools
            if t.capability is not None
            and t.capability.startswith(f"{family}.")
            and not t.requires_approval
            and t.capability != step_capability
        ]

        seen: set[str] = set()
        result: list[dict] = []
        for t in [*primary, *related_read]:
            if t.name not in seen:
                seen.add(t.name)
                result.append(
                    {
                        "name": t.name,
                        "description": t.description or t.name,
                        "input_schema": t.input_schema or {"type": "object"},
                    }
                )
        return result

    async def is_read_capability(self, capability: str) -> bool:
        """True when ALL tools for *capability* are read-only (no approval needed).

        Note: ``all()`` on an empty iterable returns ``True``.
        """
        tools = await self.resolve(capability)
        return all(not t.requires_approval for t in tools)

    async def is_write_capability(self, capability: str) -> bool:
        """True when ANY tool for *capability* requires approval."""
        tools = await self.resolve(capability)
        return any(t.requires_approval for t in tools)


async def route_step(step_capability: str, resolver: CapabilityResolver) -> str:
    """Map a plan-step capability to the agent that should execute it.

    Routing priority:
    1. ``"reason"`` / ``"respond"`` / ``"none"`` -> ``"presenter"``
    2. ``"knowledge.*"`` -> ``"librarian"``
    3. Known read capability (tools exist, none need approval) -> ``"perceiver"``
    4. Known write capability (any tool needs approval) -> ``"operator"``
    5. Unknown capability (no tools found) -> ``"operator"`` (fallback)

    ``"perceiver"`` handles information gathering (merged from Observer + Researcher).
    """
    if step_capability in ("reason", "respond", "none"):
        return "presenter"

    if step_capability.startswith("knowledge."):
        return "librarian"

    # Check if any tools exist for this capability before read/write classification
    tools = await resolver.resolve(step_capability)
    if not tools:
        logger.warning(
            "No tools found for capability %s — cannot route step",
            step_capability,
        )
        return ""  # Empty string signals unroutable

    if all(not t.requires_approval for t in tools):
        return "perceiver"

    if any(t.requires_approval for t in tools):
        return "operator"

    return "operator"
