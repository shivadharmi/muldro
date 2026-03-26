"""Centralized tool risk classification and input summarization.

Consolidates risk/write/blocked classification and approval summary
generation. Uses the capability catalog for capability-aware classification
with trust-tier context, falling back to hardcoded sets when DB unavailable.
"""

import logging

from src.integrations.capabilities import CAPABILITY_CATALOG, get_capability_for_tool

logger = logging.getLogger(__name__)

# Fallback sets — used ONLY when ToolRegistry DB is unavailable.
# Includes both native connector tool names AND raw MCP tool names
# (MCP names match after FastMCP prefix stripping in the dispatch chain).
FALLBACK_WRITE_TOOLS = frozenset(
    {
        # Native connector names
        "gmail_send",
        "gmail_send_email",
        "gmail_draft",
        "gmail_create_draft",
        "gmail_reply",
        "calendar_create",
        "calendar_create_event",
        "calendar_update",
        "calendar_update_event",
        "calendar_delete",
        "drive_create",
        "docs_create",
        "sheets_update",
        "slack_post_message",
        "slack_send_message",
        "slack_react",
        "slack_update_message",
        "github_create_issue",
        "github_comment",
        "github_create_pr",
        "github_merge_pr",
        "linear_create_issue",
        "linear_update_issue",
        "linear_comment",
        "notion_create_page",
        "notion_update_page",
        "jira_create_issue",
        "jira_update_issue",
        "jira_transition",
        "jira_comment",
        "whatsapp_send_message",
        "whatsapp_send_template",
        "sms_send_sms",
        "linkedin_create_post",
        "linkedin_share_article",
        "twitter_create_tweet",
        "twitter_reply",
        "twitter_retweet",
        # GitHub MCP raw tool names (official Go server)
        "issue_write",
        "add_issue_comment",
        "create_pull_request",
        "merge_pull_request",
        "update_pull_request",
        "sub_issue_write",
        "pull_request_review_write",
        # Slack MCP raw tool names
        "slack_reply_to_thread",
        "slack_add_reaction",
        # Google Workspace MCP raw tool names (camelCase)
        "sendGmailDraft",
        "createGmailDraft",
        "createCalendarEvent",
        "updateCalendarEvent",
        # Linear MCP raw tool names
        "linear_create_issue",
        "linear_edit_issue",
        "linear_create_comment",
        # Notion MCP raw tool names (kebab-case)
        "create-a-page",
        "update-a-page",
        "create-a-comment",
        # Atlassian MCP raw tool names (official Rovo, camelCase)
        "createJiraIssue",
        "editJiraIssue",
        "transitionJiraIssue",
        "addCommentToJiraIssue",
        "addWorklogToJiraIssue",
        # Internal
        "send_telegram",
        "send_approval_prompt",
        "approve_action",
    }
)

FALLBACK_BLOCKED_TOOLS = frozenset(
    {
        "gmail_delete",
        "drive_delete",
        "calendar_delete_event",
        # MCP equivalents
        "deleteGmailMessage",
        "deleteFile",
        "deleteCalendarEvent",
        "linear_delete_issue",
    }
)

_HIGH_RISK_TOOLS = frozenset(
    {
        "gmail_send",
        "gmail_send_email",
        "github_merge_pr",
        "slack_post_message",
        "whatsapp_send_message",
        "sms_send_sms",
        "linkedin_create_post",
        "twitter_create_tweet",
        # MCP equivalents
        "sendGmailDraft",
        "merge_pull_request",
        "slack_post_message",
        "issue_write",
        "createJiraIssue",
    }
)


class ToolClassification:
    """Result of classifying a tool's risk profile."""

    __slots__ = ("is_blocked", "is_write", "risk_level")

    def __init__(self, is_blocked: bool, is_write: bool, risk_level: str):
        self.is_blocked = is_blocked
        self.is_write = is_write
        self.risk_level = risk_level


class ToolPolicy:
    """Classifies tools and summarizes inputs for approvals."""

    async def classify(
        self,
        tool_name: str,
        *,
        db_factory=None,
        trust_tier: str | None = None,
    ) -> ToolClassification:
        """Classify a tool — capability-aware, DB-first with hardcoded fallback.

        Args:
            tool_name: The tool to classify.
            db_factory: DB session factory for registry lookup.
            trust_tier: Trust tier of the server (T0-T3). Higher tiers get
                        stricter classification.
        """
        # Try capability-based classification first
        cap_result = self._classify_via_capability(tool_name, trust_tier)
        if cap_result:
            return cap_result

        if db_factory:
            try:
                return await self._classify_via_registry(tool_name, db_factory)
            except Exception:
                pass
        return self._classify_fallback(tool_name)

    def _classify_via_capability(
        self, tool_name: str, trust_tier: str | None
    ) -> ToolClassification | None:
        """Classify using the capability catalog and trust tier."""
        capability = get_capability_for_tool(tool_name)
        if not capability:
            return None

        meta = CAPABILITY_CATALOG.get(capability)
        if not meta:
            return None

        # Read-only capabilities are always safe
        if meta.read_only:
            return ToolClassification(False, False, "low")

        # Trust tier escalation: T3 (user-added) tools always require approval
        risk = meta.risk_level
        if trust_tier == "T3":
            return ToolClassification(False, True, "high" if risk == "low" else risk)
        if trust_tier == "T2":
            is_write = risk in ("medium", "high", "critical")
            return ToolClassification(False, is_write, risk)

        # T0/T1 or unknown tier: use catalog risk
        is_write = not meta.read_only and risk in ("medium", "high", "critical")
        is_blocked = risk == "critical"
        return ToolClassification(is_blocked, is_write, risk)

    async def _classify_via_registry(self, tool_name: str, db_factory) -> ToolClassification:
        from src.services.tool_registry import ToolRegistry

        async with db_factory() as db:
            registry = ToolRegistry(db)
            tool_def = await registry.get_tool(tool_name)
            if not tool_def:
                return ToolClassification(False, False, "low")
            return ToolClassification(
                is_blocked=not tool_def.enabled,
                is_write=tool_def.requires_approval,
                risk_level=tool_def.risk_level,
            )

    def _classify_fallback(self, tool_name: str) -> ToolClassification:
        if tool_name in FALLBACK_BLOCKED_TOOLS:
            return ToolClassification(True, False, "critical")
        if tool_name in FALLBACK_WRITE_TOOLS:
            risk = "high" if tool_name in _HIGH_RISK_TOOLS else "medium"
            return ToolClassification(False, True, risk)
        return ToolClassification(False, False, "low")

    @staticmethod
    def summarize_input(tool_name: str, tool_input: dict) -> str:
        """Create a human-readable summary of tool input for approval display."""
        if "to" in tool_input and "subject" in tool_input:
            return f"Send email to {tool_input['to']}: {tool_input.get('subject', '')}"
        if "channel" in tool_input and "text" in tool_input:
            text = str(tool_input["text"])
            truncated = text[:100] + ("..." if len(text) > 100 else "")
            return f"Post to {tool_input['channel']}: {truncated}"
        if "title" in tool_input:
            return f"{tool_name}: {tool_input['title']}"
        return f"{tool_name} with {len(tool_input)} parameters"
