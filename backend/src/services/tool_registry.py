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
    canonical: str | None = None,
) -> dict:
    return {
        "name": name,
        "risk_level": risk,
        "requires_approval": approval,
        "connector_type": connector,
        "enabled": enabled,
        "canonical_name": canonical,
    }


# Canonical name mappings — aliases point to canonical tool names.
# resolve_canonical() uses these to normalize tool names at call time.
CANONICAL_ALIASES: dict[str, str] = {
    "gmail_send_email": "gmail_send",
    "gmail_draft": "gmail_create_draft",
    "calendar_create": "calendar_create_event",
    "calendar_update": "calendar_update_event",
    "calendar_delete": "calendar_delete_event",
    "slack_post_message": "slack_send_message",
}


# Default tool definitions seeded on startup
_DEFAULT_TOOLS = [
    # Gmail writes
    _t("gmail_send", "high", True, "gmail"),
    _t("gmail_send_email", "high", True, "gmail", canonical="gmail_send"),
    _t("gmail_draft", "medium", True, "gmail", canonical="gmail_create_draft"),
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
    _t("calendar_create", "medium", True, "calendar", canonical="calendar_create_event"),
    _t("calendar_create_event", "medium", True, "calendar"),
    _t("calendar_update", "medium", True, "calendar", canonical="calendar_update_event"),
    _t("calendar_update_event", "medium", True, "calendar"),
    _t(
        "calendar_delete",
        "critical",
        True,
        "calendar",
        enabled=False,
        canonical="calendar_delete_event",
    ),
    _t("calendar_delete_event", "critical", True, "calendar", enabled=False),
    # Slack
    _t("slack_post_message", "high", True, "slack", canonical="slack_send_message"),
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
    # Communication / UI
    _t("push_ui_update", "low", False, "internal"),
    _t("send_telegram", "medium", True, "telegram"),
    _t("send_approval_prompt", "medium", True, "telegram"),
    # Research
    _t("perplexity_search", "low", False, "browser"),
    # Browser
    _t("browser_open", "medium", False, "browser"),
    _t("browser_snapshot", "low", False, "browser"),
    _t("browser_extract", "low", False, "browser"),
    _t("browser_click", "medium", False, "browser"),
    _t("browser_type", "medium", False, "browser"),
    _t("browser_submit", "high", True, "browser"),
    _t("browser_screenshot", "low", False, "browser"),
    # GitHub MCP raw tool names (official ghcr.io/github/github-mcp-server)
    _t("issue_write", "medium", True, "github"),
    _t("issue_read", "low", False, "github"),
    _t("add_issue_comment", "medium", True, "github"),
    _t("create_pull_request", "high", True, "github"),
    _t("merge_pull_request", "high", True, "github"),
    _t("update_pull_request", "medium", True, "github"),
    _t("pull_request_read", "low", False, "github"),
    _t("pull_request_review_write", "medium", True, "github"),
    _t("sub_issue_write", "medium", True, "github"),
    _t("list_issues", "low", False, "github"),
    _t("search_issues", "low", False, "github"),
    _t("search_code", "low", False, "github"),
    _t("search_repositories", "low", False, "github"),
    _t("search_users", "low", False, "github"),
    _t("search_orgs", "low", False, "github"),
    _t("get_diff", "low", False, "github"),
    _t("get_reviews", "low", False, "github"),
    _t("get_check_runs", "low", False, "github"),
    _t("get_files", "low", False, "github"),
    _t("list_pull_requests", "low", False, "github"),
    _t("search_pull_requests", "low", False, "github"),
    _t("get_sub_issues", "low", False, "github"),
    # Slack MCP raw tool names (slack-mcp-server — already prefixed)
    _t("slack_reply_to_thread", "high", True, "slack"),
    _t("slack_add_reaction", "medium", True, "slack"),
    _t("slack_get_channel_history", "low", False, "slack"),
    _t("slack_get_thread_replies", "low", False, "slack"),
    _t("slack_get_users", "low", False, "slack"),
    _t("slack_get_user_profile", "low", False, "slack"),
    # Google Workspace MCP raw tool names (camelCase)
    _t("sendGmailDraft", "high", True, "gmail"),
    _t("createGmailDraft", "medium", True, "gmail"),
    _t("listGmailMessages", "low", False, "gmail"),
    _t("readGmailMessage", "low", False, "gmail"),
    _t("searchGmail", "low", False, "gmail"),
    _t("deleteGmailMessage", "critical", True, "gmail", enabled=False),
    _t("createCalendarEvent", "medium", True, "calendar"),
    _t("updateCalendarEvent", "medium", True, "calendar"),
    _t("deleteCalendarEvent", "critical", True, "calendar", enabled=False),
    _t("listCalendarEvents", "low", False, "calendar"),
    _t("getCalendarEvent", "low", False, "calendar"),
    # Linear (native + MCP — mcp-server-linear tools already prefixed)
    _t("linear_create_issue", "medium", True, "linear"),
    _t("linear_update_issue", "medium", True, "linear"),
    _t("linear_comment", "medium", True, "linear"),
    _t("linear_list_issues", "low", False, "linear"),
    _t("linear_get_issue", "low", False, "linear"),
    # Linear MCP-specific tool names
    _t("linear_edit_issue", "medium", True, "linear"),
    _t("linear_create_comment", "medium", True, "linear"),
    _t("linear_delete_issue", "critical", True, "linear", enabled=False),
    _t("linear_search_issues", "low", False, "linear"),
    _t("linear_get_teams", "low", False, "linear"),
    # Notion (native connector names)
    _t("notion_create_page", "medium", True, "notion"),
    _t("notion_update_page", "medium", True, "notion"),
    _t("notion_search", "low", False, "notion"),
    _t("notion_get_page", "low", False, "notion"),
    # Notion MCP raw tool names (@notionhq/notion-mcp-server, kebab-case)
    _t("create-a-page", "medium", True, "notion"),
    _t("update-a-page", "medium", True, "notion"),
    _t("retrieve-a-page", "low", False, "notion"),
    _t("search", "low", False, "notion"),
    _t("query-data-source", "low", False, "notion"),
    _t("create-a-comment", "medium", True, "notion"),
    _t("append-block-children", "medium", True, "notion"),
    # Jira (native connector names)
    _t("jira_create_issue", "medium", True, "jira"),
    _t("jira_update_issue", "medium", True, "jira"),
    _t("jira_transition", "medium", True, "jira"),
    _t("jira_comment", "medium", True, "jira"),
    _t("jira_list_issues", "low", False, "jira"),
    _t("jira_get_issue", "low", False, "jira"),
    # Atlassian MCP raw tool names (official Rovo MCP, camelCase)
    _t("getJiraIssue", "low", False, "jira"),
    _t("searchJiraIssuesUsingJql", "low", False, "jira"),
    _t("getVisibleJiraProjects", "low", False, "jira"),
    _t("getJiraIssueTypeMetaWithFields", "low", False, "jira"),
    _t("getJiraProjectIssueTypesMetadata", "low", False, "jira"),
    _t("getTransitionsForJiraIssue", "low", False, "jira"),
    _t("lookupJiraAccountId", "low", False, "jira"),
    _t("getJiraIssueRemoteIssueLinks", "low", False, "jira"),
    _t("createJiraIssue", "medium", True, "jira"),
    _t("editJiraIssue", "medium", True, "jira"),
    _t("transitionJiraIssue", "medium", True, "jira"),
    _t("addCommentToJiraIssue", "medium", True, "jira"),
    _t("addWorklogToJiraIssue", "medium", True, "jira"),
    # WhatsApp
    _t("whatsapp_send_message", "high", True, "whatsapp"),
    _t("whatsapp_send_template", "high", True, "whatsapp"),
    _t("whatsapp_mark_read", "low", False, "whatsapp"),
    # SMS / Twilio
    _t("sms_send_sms", "high", True, "sms"),
    # LinkedIn
    _t("linkedin_create_post", "high", True, "linkedin"),
    _t("linkedin_share_article", "high", True, "linkedin"),
    _t("linkedin_get_profile", "low", False, "linkedin"),
    # Twitter / X
    _t("twitter_create_tweet", "high", True, "twitter"),
    _t("twitter_reply", "high", True, "twitter"),
    _t("twitter_retweet", "medium", True, "twitter"),
    _t("twitter_get_mentions", "low", False, "twitter"),
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
                canonical_name=tool_data.get("canonical_name"),
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

    def resolve_canonical(self, tool_name: str) -> str:
        """Resolve a tool name to its canonical form.

        Checks in-memory CANONICAL_ALIASES first, then falls back to DB
        canonical_name field. Returns the original name if no alias found.
        """
        return CANONICAL_ALIASES.get(tool_name, tool_name)

    async def resolve_canonical_db(self, tool_name: str) -> str:
        """Resolve via DB lookup — for cases where in-memory map is insufficient."""
        canonical = CANONICAL_ALIASES.get(tool_name)
        if canonical:
            return canonical
        tool = await self.get_tool(tool_name)
        if tool and tool.canonical_name:
            return tool.canonical_name
        return tool_name

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
