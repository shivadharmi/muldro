"""ToolRegistry — DB-backed tool definitions replacing hardcoded sets."""

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID

from src.models.tool_definitions import ToolDefinition

logger = logging.getLogger(__name__)


def _t(
    name: str,
    risk: str = "low",
    approval: bool = False,
    connector: str | None = None,
    enabled: bool = True,
) -> dict:
    return {
        "name": name,
        "risk_level": risk,
        "requires_approval": approval,
        "connector_type": connector,
        "enabled": enabled,
    }


# Default tool definitions seeded on startup
_DEFAULT_TOOLS = [
    # Gmail writes
    _t("gmail_send", "high", True, "gmail"),
    _t("gmail_send_email", "high", True, "gmail"),
    _t("gmail_draft", "medium", True, "gmail"),
    _t("gmail_create_draft", "medium", True, "gmail"),
    _t("gmail_reply", "high", True, "gmail"),
    # Gmail reads
    _t("gmail_list", "low", False, "gmail"),
    _t("gmail_read", "low", False, "gmail"),
    _t("gmail_search", "low", False, "gmail"),
    # Gmail blocked
    _t("gmail_delete", "critical", True, "gmail", enabled=False),
    # Calendar
    _t("calendar_list", "low", False, "calendar"),
    _t("calendar_get", "low", False, "calendar"),
    _t("calendar_create", "medium", True, "calendar"),
    _t("calendar_create_event", "medium", True, "calendar"),
    _t("calendar_update", "medium", True, "calendar"),
    _t("calendar_update_event", "medium", True, "calendar"),
    _t("calendar_delete", "critical", True, "calendar", enabled=False),
    _t("calendar_delete_event", "critical", True, "calendar", enabled=False),
    # Slack
    _t("slack_post_message", "high", True, "slack"),
    _t("slack_send_message", "high", True, "slack"),
    _t("slack_react", "medium", True, "slack"),
    _t("slack_update_message", "medium", True, "slack"),
    _t("slack_list_channels", "low", False, "slack"),
    _t("slack_get_messages", "low", False, "slack"),
    _t("slack_search", "low", False, "slack"),
    # GitHub
    _t("github_create_issue", "medium", True, "github"),
    _t("github_comment", "medium", True, "github"),
    _t("github_create_pr", "high", True, "github"),
    _t("github_merge_pr", "high", True, "github"),
    # Drive
    _t("drive_list", "low", False, "drive"),
    _t("drive_search", "low", False, "drive"),
    _t("drive_create", "medium", True, "drive"),
    _t("drive_delete", "critical", True, "drive", enabled=False),
    # Internal intelligence tools (read-only)
    _t("search_memory", "low", False, "internal"),
    _t("get_entities", "low", False, "internal"),
    _t("get_active_plans", "low", False, "internal"),
    _t("get_briefing", "low", False, "internal"),
    _t("get_observation_cursor", "low", False, "internal"),
    _t("report_observation", "low", False, "internal"),
    _t("get_task", "low", False, "internal"),
    _t("get_goals", "low", False, "internal"),
    _t("build_context", "low", False, "internal"),
    # Internal intelligence tools (write)
    _t("ingest_event", "low", False, "internal"),
    _t("update_entity", "low", False, "internal"),
    _t("plan_command", "low", False, "internal"),
    _t("evaluate_policy", "low", False, "internal"),
    _t("approve_action", "medium", True, "internal"),
    _t("update_observation_cursor", "low", False, "internal"),
    _t("extract_preferences", "low", False, "internal"),
    _t("create_task", "low", False, "internal"),
    _t("verify_run", "low", False, "internal"),
    _t("update_execution", "low", False, "internal"),
    # Communication
    _t("send_telegram", "medium", True, "telegram"),
    _t("send_approval_prompt", "medium", True, "telegram"),
    # Browser
    _t("browser_open", "medium", False, "browser"),
    _t("browser_snapshot", "low", False, "browser"),
    _t("browser_extract", "low", False, "browser"),
    _t("browser_click", "medium", False, "browser"),
    _t("browser_type", "medium", False, "browser"),
    _t("browser_submit", "high", True, "browser"),
    _t("browser_screenshot", "low", False, "browser"),
]


class ToolRegistry:
    """DB-backed registry of all available tools and their metadata."""

    def __init__(self, db: AsyncSession):
        self._db = db
        self._cache: dict[str, ToolDefinition] = {}

    async def seed_defaults(self, workspace_id: str = "") -> int:
        """Seed default tool definitions if they don't exist. Returns count added."""
        added = 0
        for tool_data in _DEFAULT_TOOLS:
            existing = await self._db.execute(
                select(ToolDefinition).where(ToolDefinition.name == tool_data["name"])
            )
            if existing.scalar_one_or_none():
                continue
            tool = ToolDefinition(
                tool_id=f"tool_{ULID()}",
                workspace_id=workspace_id,
                name=tool_data["name"],
                risk_level=tool_data.get("risk_level", "low"),
                requires_approval=tool_data.get("requires_approval", False),
                connector_type=tool_data.get("connector_type"),
                enabled=tool_data.get("enabled", True),
            )
            self._db.add(tool)
            added += 1

        if added:
            await self._db.flush()
            logger.info("Seeded %d tool definitions", added)
        return added

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
