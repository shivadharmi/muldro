"""Sub-agent definitions for the Jarvis orchestrator.

Defines 8 specialized agents with their prompts, model assignments,
tool access scopes, and per-agent thinking configuration.
"""

from dataclasses import dataclass, field

from src.orchestrator.prompts import AGENT_PROMPTS

# Known MCP server prefixes (server name with hyphens replaced by underscores + _)
# Used to normalize namespaced tool names like "google_workspace_gmail_list" → "gmail_list"
_MCP_PREFIXES = (
    "google_workspace_",
    "github_",
    "slack_",
    "playwright_",
    "filesystem_",
    "linear_",
    "notion_",
    "atlassian_",
    "twilio_",
)


def _strip_mcp_prefix(tool_name: str) -> str:
    """Strip known MCP server prefix from a tool name."""
    for prefix in _MCP_PREFIXES:
        if tool_name.startswith(prefix):
            return tool_name[len(prefix) :]
    return tool_name


# Model tier assignments per agent
AGENT_MODEL_TIERS = {
    "observer": "sonnet",
    "librarian": "sonnet",
    "planner": "opus",
    "governor": "sonnet",
    "operator": "sonnet",
    "presenter": "sonnet",
    "researcher": "sonnet",
    "persona": "haiku",
}

# Tool access scopes per agent — defines which tools each agent can use.
#
# Tool names listed here must match EITHER:
#   1. Native connector/intelligence tool names (e.g. "gmail_send_email")
#   2. Raw MCP tool names BEFORE FastMCP prefix (these match after _strip_mcp_prefix)
#
# FastMCP namespaces tools as "servername_rawtool". The can_use_tool() method
# strips known prefixes and re-checks, so listing the raw MCP name suffices.
#
# Actual MCP server tool names (verified from package source):
#   GitHub (ghcr.io/github/github-mcp-server — official Go server):
#     issue_read, issue_write, list_issues, search_issues, add_issue_comment,
#     pull_request_read, create_pull_request, merge_pull_request, update_pull_request,
#     search_code, search_repositories, get_diff, get_reviews, get_check_runs, etc.
#   Slack (slack-mcp-server):
#     slack_list_channels, slack_post_message, slack_reply_to_thread,
#     slack_add_reaction, slack_get_channel_history, slack_get_thread_replies,
#     slack_get_users, slack_get_user_profile
#   Google Workspace (google-workspace-mcp, camelCase):
#     listGmailMessages, readGmailMessage, searchGmail, createGmailDraft,
#     sendGmailDraft, listCalendarEvents, createCalendarEvent, etc.
#   Linear (mcp-server-linear, already prefixed "linear_"):
#     linear_create_issue, linear_edit_issue, linear_search_issues,
#     linear_get_issue, linear_create_comment, linear_get_teams, etc.
#   Notion (@notionhq/notion-mcp-server, kebab-case):
#     create-a-page, retrieve-a-page, update-a-page, search,
#     query-data-source, create-a-comment, append-block-children, etc.
#   Atlassian (mcp.atlassian.com — official Rovo MCP, camelCase):
#     getJiraIssue, searchJiraIssuesUsingJql, getVisibleJiraProjects,
#     createJiraIssue, editJiraIssue, transitionJiraIssue, addCommentToJiraIssue,
#     addWorklogToJiraIssue, getTransitionsForJiraIssue, lookupJiraAccountId, etc.
#   Twilio (@twilio-alpha/mcp): dynamic from OpenAPI

AGENT_TOOL_SCOPES: dict[str, set[str]] = {
    "observer": {
        # --- Native connector reads ---
        "gmail_list",
        "gmail_read",
        "gmail_search",
        "calendar_list",
        "calendar_get",
        "drive_list",
        "drive_search",
        "slack_list_channels",
        "slack_get_messages",
        "slack_search",
        "linear_list_issues",
        "linear_get_issue",
        "notion_search",
        "notion_get_page",
        "jira_list_issues",
        "jira_get_issue",
        "twitter_get_mentions",
        # --- MCP raw tool names (match after prefix strip) ---
        # GitHub MCP (official Go server)
        "list_issues",
        "issue_read",
        "search_issues",
        "search_code",
        # Slack MCP (already prefixed "slack_")
        "slack_get_channel_history",
        "slack_get_thread_replies",
        "slack_get_users",
        "slack_get_user_profile",
        # Google Workspace MCP (camelCase)
        "listGmailMessages",
        "readGmailMessage",
        "searchGmail",
        "listCalendarEvents",
        "getCalendarEvent",
        "listGoogleDocs",
        "searchGoogleDocs",
        # Linear MCP (already prefixed "linear_")
        "linear_search_issues",
        "linear_search_issues_by_identifier",
        # Notion MCP (kebab-case)
        "search",
        "query-data-source",
        "retrieve-a-page",
        "retrieve-a-database",
        "get-page-content",
        # Atlassian MCP (official Rovo, camelCase)
        "getJiraIssue",
        "searchJiraIssuesUsingJql",
        "getVisibleJiraProjects",
        "getTransitionsForJiraIssue",
        # --- Internal ---
        "ingest_event",
        "report_observation",
        "get_observation_cursor",
        "update_observation_cursor",
    },
    "librarian": {
        "update_entity",
        "get_entities",
        "search_memory",
    },
    "planner": {
        "plan_command",
        "get_active_plans",
        "search_memory",
        "get_entities",
    },
    "governor": {
        "evaluate_policy",
        "approve_action",
        "report_governor_verdict",
    },
    "operator": {
        # --- Native connector writes (canonical names only) ---
        "gmail_send",
        "gmail_create_draft",
        "gmail_reply",
        "calendar_create_event",
        "calendar_update_event",
        "slack_send_message",
        "slack_update_message",
        "slack_react",
        "github_comment",
        "github_create_issue",
        "github_create_pr",
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
        # --- MCP raw tool names (match after prefix strip) ---
        # GitHub MCP (official Go server)
        "issue_write",
        "add_issue_comment",
        "create_pull_request",
        "merge_pull_request",
        "update_pull_request",
        "sub_issue_write",
        "pull_request_review_write",
        # Slack MCP
        "slack_reply_to_thread",
        "slack_add_reaction",
        # Google Workspace MCP
        "createGmailDraft",
        "sendGmailDraft",
        "createCalendarEvent",
        "updateCalendarEvent",
        # Linear MCP (linear_create_issue already listed above)
        "linear_edit_issue",
        "linear_create_comment",
        "linear_bulk_update_issues",
        # Notion MCP
        "create-a-page",
        "update-a-page",
        "create-a-comment",
        "append-block-children",
        # Atlassian MCP (official Rovo, camelCase)
        "createJiraIssue",
        "editJiraIssue",
        "transitionJiraIssue",
        "addCommentToJiraIssue",
        "addWorklogToJiraIssue",
        # --- Internal ---
        "update_execution",
    },
    "presenter": {
        "get_briefing",
        "search_memory",
        "get_entities",
        # Communication
        "send_telegram",
        "send_approval_prompt",
        "push_ui_update",
        # Messaging delivery channels
        "whatsapp_send_message",
        "sms_send_sms",
    },
    "researcher": {
        # --- Native connector reads ---
        "search_memory",
        "get_entities",
        "gmail_list",
        "gmail_read",
        "gmail_search",
        "calendar_list",
        "calendar_get",
        "drive_list",
        "drive_search",
        "slack_list_channels",
        "slack_get_messages",
        "slack_search",
        "linear_list_issues",
        "linear_get_issue",
        "notion_search",
        "notion_get_page",
        "jira_list_issues",
        "jira_get_issue",
        "linkedin_get_profile",
        "twitter_get_mentions",
        # --- MCP raw tool names ---
        # GitHub MCP (official Go server)
        "list_issues",
        "issue_read",
        "search_issues",
        "search_code",
        "search_repositories",
        "pull_request_read",
        "get_diff",
        "get_reviews",
        # Slack MCP
        "slack_get_channel_history",
        "slack_get_thread_replies",
        "slack_get_users",
        # Google Workspace MCP
        "listGmailMessages",
        "readGmailMessage",
        "searchGmail",
        "listCalendarEvents",
        "getCalendarEvent",
        "listGoogleDocs",
        "searchGoogleDocs",
        # Linear MCP
        "linear_search_issues",
        "linear_get_issue",
        # Notion MCP
        "search",
        "query-data-source",
        "retrieve-a-page",
        "get-page-content",
        # Atlassian MCP (official Rovo, camelCase)
        "getJiraIssue",
        "searchJiraIssuesUsingJql",
        "getVisibleJiraProjects",
        "getTransitionsForJiraIssue",
        # Web research
        "perplexity_search",
        # Browser
        "playwright_navigate",
        "playwright_screenshot",
        "playwright_get_text",
    },
    "persona": {
        "search_memory",
        "extract_preferences",
    },
}


@dataclass
class ThinkingConfig:
    """Per-agent thinking configuration."""

    enabled: bool = True
    budget_tokens: int = 4096


# Per-agent thinking assignments
AGENT_THINKING: dict[str, ThinkingConfig] = {
    "planner": ThinkingConfig(enabled=True, budget_tokens=8192),
    "researcher": ThinkingConfig(enabled=True, budget_tokens=6144),
    "librarian": ThinkingConfig(enabled=True, budget_tokens=4096),
    "presenter": ThinkingConfig(enabled=True, budget_tokens=4096),
    "governor": ThinkingConfig(enabled=True, budget_tokens=2048),
    "operator": ThinkingConfig(enabled=True, budget_tokens=2048),
    "observer": ThinkingConfig(enabled=True, budget_tokens=2048),
    "persona": ThinkingConfig(enabled=False, budget_tokens=0),
}


@dataclass
class SubAgent:
    """Definition of a Jarvis sub-agent."""

    name: str
    prompt: str
    model_tier: str  # opus, sonnet, haiku
    tool_scope: set[str] = field(default_factory=set)
    max_tokens: int = 4096
    temperature: float = 0.3
    thinking: ThinkingConfig = field(default_factory=ThinkingConfig)

    def can_use_tool(self, tool_name: str) -> bool:
        """Check if this agent is allowed to use a specific tool.

        Handles MCP namespaced tools: FastMCP prefixes tools with
        ``servername_`` (e.g. ``google_workspace_gmail_list``).
        We match against both the full name and the suffix after
        stripping known MCP server prefixes.
        """
        if tool_name in self.tool_scope:
            return True
        # Strip MCP server prefix and re-check
        short = _strip_mcp_prefix(tool_name)
        return short != tool_name and short in self.tool_scope


def create_sub_agents() -> dict[str, SubAgent]:
    """Create all 8 sub-agent definitions."""
    agents = {}
    for name, prompt in AGENT_PROMPTS.items():
        agents[name] = SubAgent(
            name=name,
            prompt=prompt,
            model_tier=AGENT_MODEL_TIERS.get(name, "sonnet"),
            tool_scope=AGENT_TOOL_SCOPES.get(name, set()),
            max_tokens=8192 if name == "planner" else 4096,
            temperature=0.1 if name == "governor" else 0.3,
            thinking=AGENT_THINKING.get(name, ThinkingConfig()),
        )
    return agents


# Pre-built agent registry
AGENTS = create_sub_agents()
