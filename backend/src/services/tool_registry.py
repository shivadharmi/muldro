"""ToolRegistry — DB-backed tool definitions replacing hardcoded sets."""

import logging

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID

from src.models.tool_definitions import ToolDefinition
from src.tools.catalog import EXTERNAL_TOOL_SEEDS, INTERNAL_TOOLS

logger = logging.getLogger(__name__)


def _schema_for_claude(model_cls: type[BaseModel]) -> dict:
    """Generate input schema excluding runtime-injected fields.

    User_id and workspace_id are injected at runtime by the orchestrator,
    so they should not be presented to Claude in the tool schema.
    """
    schema = model_cls.model_json_schema()
    for field in ("user_id", "workspace_id"):
        schema.get("properties", {}).pop(field, None)
        if "required" in schema and field in schema["required"]:
            schema["required"].remove(field)
    return schema


class ToolRegistry:
    """DB-backed registry of all available tools and their metadata."""

    def __init__(self, db: AsyncSession):
        self._db = db
        self._cache: dict[str, ToolDefinition] = {}

    async def seed_defaults(self, workspace_id: str | None = None) -> int:
        """Seed or update default tool definitions. Returns count created/updated.

        Two-pass seeding strategy:
        1. Seed from INTERNAL_TOOLS catalog (19 tools)
        2. Seed from EXTERNAL_TOOL_SEEDS catalog (137+ tools)

        For existing tools, syncs backend, source, server, capability,
        risk_level, requires_approval, and verified fields.
        """
        # Build lookup of existing tools by name
        result = await self._db.execute(select(ToolDefinition))
        existing = {t.name: t for t in result.scalars().all()}

        seen: set[str] = set()
        changed = 0

        # Pass 1: Seed from INTERNAL_TOOLS catalog
        for tool in INTERNAL_TOOLS:
            name = tool.name
            if name in seen:
                continue
            seen.add(name)

            backend = "internal_mcp"
            source = "internal"
            server = tool.server
            capability = tool.capability
            risk = tool.risk_level
            approval = tool.requires_approval
            verified = True  # Internal tools are always verified
            input_schema = _schema_for_claude(tool.input_model)

            if name not in existing:
                new_tool = ToolDefinition(
                    tool_id=f"tool_{ULID()}",
                    workspace_id=workspace_id,
                    name=name,
                    backend=backend,
                    source=source,
                    server=server,
                    risk_level=risk,
                    requires_approval=approval,
                    connector_type="internal",
                    capability=capability,
                    verified=verified,
                    input_schema=input_schema,
                    enabled=True,
                )
                self._db.add(new_tool)
                changed += 1
                continue

            # Sync mutable fields if they diverged
            tool_def = existing[name]
            needs_update = False

            if tool_def.backend != backend:
                tool_def.backend = backend
                needs_update = True
            if tool_def.source != source:
                tool_def.source = source
                needs_update = True
            if tool_def.server != server:
                tool_def.server = server
                needs_update = True
            if tool_def.capability != capability:
                tool_def.capability = capability
                needs_update = True
            if tool_def.risk_level != risk:
                tool_def.risk_level = risk
                needs_update = True
            if tool_def.requires_approval != approval:
                tool_def.requires_approval = approval
                needs_update = True
            if tool_def.verified != verified:
                tool_def.verified = verified
                needs_update = True
            if tool_def.input_schema != input_schema:
                tool_def.input_schema = input_schema
                needs_update = True

            if needs_update:
                changed += 1

        # Pass 2: Seed from EXTERNAL_TOOL_SEEDS catalog
        for seed in EXTERNAL_TOOL_SEEDS:
            name = seed.name
            if name in seen:
                continue
            seen.add(name)

            backend = "composite" if seed.server == "_composite" else "external_mcp"
            source = "seed"
            server = seed.server
            capability = seed.capability
            risk = seed.risk_level
            approval = seed.requires_approval
            verified = seed.verified
            connector = seed.server  # Use server name as connector_type for backward compat

            if name not in existing:
                new_tool = ToolDefinition(
                    tool_id=f"tool_{ULID()}",
                    workspace_id=workspace_id,
                    name=name,
                    backend=backend,
                    source=source,
                    server=server,
                    risk_level=risk,
                    requires_approval=approval,
                    connector_type=connector,
                    capability=capability,
                    verified=verified,
                    enabled=True,
                )
                self._db.add(new_tool)
                changed += 1
                continue

            # Sync mutable fields if they diverged
            tool_def = existing[name]
            needs_update = False

            if tool_def.backend != backend:
                tool_def.backend = backend
                needs_update = True
            if tool_def.source != source:
                tool_def.source = source
                needs_update = True
            if tool_def.server != server:
                tool_def.server = server
                needs_update = True
            if tool_def.capability != capability:
                tool_def.capability = capability
                needs_update = True
            if tool_def.risk_level != risk:
                tool_def.risk_level = risk
                needs_update = True
            if tool_def.requires_approval != approval:
                tool_def.requires_approval = approval
                needs_update = True
            if tool_def.verified != verified:
                tool_def.verified = verified
                needs_update = True

            if needs_update:
                changed += 1

        if changed:
            await self._db.flush()
            logger.info("Seeded/updated %d tool definitions", changed)
        return changed

    async def register_tool(
        self,
        name: str,
        risk_level: str = "low",
        requires_approval: bool = False,
        connector_type: str | None = None,
        description: str | None = None,
        input_schema: dict | None = None,
        output_schema: dict | None = None,
        timeout_seconds: int = 30,
        idempotent: bool = False,
        workspace_id: str = "",
    ) -> ToolDefinition:
        existing = await self._db.execute(select(ToolDefinition).where(ToolDefinition.name == name))
        tool = existing.scalar_one_or_none()
        if tool:
            tool.risk_level = risk_level
            tool.requires_approval = requires_approval
            tool.connector_type = connector_type
            if description:
                tool.description = description
            await self._db.flush()
            return tool

        tool = ToolDefinition(
            tool_id=f"tool_{ULID()}",
            workspace_id=workspace_id,
            name=name,
            risk_level=risk_level,
            requires_approval=requires_approval,
            connector_type=connector_type,
            description=description,
            input_schema=input_schema,
            output_schema=output_schema,
            timeout_seconds=timeout_seconds,
            idempotent=idempotent,
        )
        self._db.add(tool)
        await self._db.flush()
        self._cache[name] = tool
        return tool

    async def get_tool(self, name: str) -> ToolDefinition | None:
        if name in self._cache:
            return self._cache[name]
        result = await self._db.execute(select(ToolDefinition).where(ToolDefinition.name == name))
        tool = result.scalar_one_or_none()
        if tool:
            self._cache[name] = tool
        return tool

    async def list_tools(
        self,
        connector_type: str | None = None,
        enabled_only: bool = True,
    ) -> list[ToolDefinition]:
        stmt = select(ToolDefinition)
        if connector_type:
            stmt = stmt.where(ToolDefinition.connector_type == connector_type)
        if enabled_only:
            stmt = stmt.where(ToolDefinition.enabled.is_(True))
        stmt = stmt.order_by(ToolDefinition.name)
        result = await self._db.execute(stmt)
        return list(result.scalars().all())

    async def is_write_tool(self, name: str) -> bool:
        tool = await self.get_tool(name)
        if not tool:
            return False
        return tool.requires_approval

    async def is_blocked_tool(self, name: str) -> bool:
        tool = await self.get_tool(name)
        if not tool:
            return False
        return not tool.enabled

    async def classify_risk(self, name: str) -> str:
        tool = await self.get_tool(name)
        if not tool:
            return "low"
        return tool.risk_level

    async def list_for_task_type(self, task_type: str) -> list[ToolDefinition]:
        """List tools relevant for a given task type."""
        type_to_connectors = {
            "draft_email": ["gmail"],
            "send_email": ["gmail"],
            "create_event": ["calendar"],
            "post_message": ["slack"],
            "create_issue": ["github"],
            "research": ["internal", "browser"],
            "browse": ["browser"],
        }
        connectors = type_to_connectors.get(task_type, ["internal"])
        tools = []
        for ct in connectors:
            tools.extend(await self.list_tools(connector_type=ct))
        return tools
