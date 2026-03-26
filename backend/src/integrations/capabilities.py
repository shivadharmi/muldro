"""Canonical capability catalog for Jarvis integrations.

Every tool Jarvis can invoke maps to a canonical capability string (e.g. "email.send",
"calendar.list", "repo.create_pr"). The catalog is the single source of truth for:

1. CapabilityFamily — the 10 top-level capability families
2. CAPABILITY_CATALOG — capability string → CapabilityMeta (family, read_only, risk_level)
3. TOOL_TO_CAPABILITY — every known tool name → canonical capability
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

try:  # Python 3.11+
    from enum import StrEnum
except ImportError:  # Python 3.10 fallback

    class StrEnum(str, Enum):
        """Compatibility fallback for enum.StrEnum on Python < 3.11."""


class CapabilityFamily(StrEnum):
    EMAIL = "email"
    CALENDAR = "calendar"
    REPO = "repo"
    ISSUE = "issue"
    DOC = "doc"
    WORKFLOW = "workflow"
    MESSAGING = "messaging"
    BROWSER = "browser"
    SEARCH = "search"
    INTERNAL = "internal"


@dataclass(frozen=True, slots=True)
class CapabilityMeta:
    family: CapabilityFamily
    read_only: bool
    risk_level: str  # low, medium, high, critical


def _cap(family: CapabilityFamily, read_only: bool, risk: str = "low") -> CapabilityMeta:
    return CapabilityMeta(family=family, read_only=read_only, risk_level=risk)


# ── Capability catalog ──────────────────────────────────────────────────────

CAPABILITY_CATALOG: dict[str, CapabilityMeta] = {
    # Email
    "email.send": _cap(CapabilityFamily.EMAIL, False, "high"),
    "email.draft": _cap(CapabilityFamily.EMAIL, False, "medium"),
    "email.reply": _cap(CapabilityFamily.EMAIL, False, "high"),
    "email.list": _cap(CapabilityFamily.EMAIL, True),
    "email.read": _cap(CapabilityFamily.EMAIL, True),
    "email.search": _cap(CapabilityFamily.EMAIL, True),
    "email.delete": _cap(CapabilityFamily.EMAIL, False, "critical"),
    # Calendar
    "calendar.list": _cap(CapabilityFamily.CALENDAR, True),
    "calendar.get": _cap(CapabilityFamily.CALENDAR, True),
    "calendar.create": _cap(CapabilityFamily.CALENDAR, False, "medium"),
    "calendar.update": _cap(CapabilityFamily.CALENDAR, False, "medium"),
    "calendar.delete": _cap(CapabilityFamily.CALENDAR, False, "critical"),
    # Repository
    "repo.search_code": _cap(CapabilityFamily.REPO, True),
    "repo.search_repos": _cap(CapabilityFamily.REPO, True),
    "repo.get_diff": _cap(CapabilityFamily.REPO, True),
    "repo.get_files": _cap(CapabilityFamily.REPO, True),
    "repo.create_pr": _cap(CapabilityFamily.REPO, False, "high"),
    "repo.merge_pr": _cap(CapabilityFamily.REPO, False, "high"),
    "repo.update_pr": _cap(CapabilityFamily.REPO, False, "medium"),
    "repo.list_prs": _cap(CapabilityFamily.REPO, True),
    "repo.search_prs": _cap(CapabilityFamily.REPO, True),
    "repo.get_reviews": _cap(CapabilityFamily.REPO, True),
    "repo.review_pr": _cap(CapabilityFamily.REPO, False, "medium"),
    "repo.get_checks": _cap(CapabilityFamily.REPO, True),
    # Issue tracking
    "issue.create": _cap(CapabilityFamily.ISSUE, False, "medium"),
    "issue.update": _cap(CapabilityFamily.ISSUE, False, "medium"),
    "issue.comment": _cap(CapabilityFamily.ISSUE, False, "medium"),
    "issue.list": _cap(CapabilityFamily.ISSUE, True),
    "issue.get": _cap(CapabilityFamily.ISSUE, True),
    "issue.search": _cap(CapabilityFamily.ISSUE, True),
    "issue.delete": _cap(CapabilityFamily.ISSUE, False, "critical"),
    "issue.transition": _cap(CapabilityFamily.ISSUE, False, "medium"),
    "issue.sub_issue": _cap(CapabilityFamily.ISSUE, False, "medium"),
    # Document / knowledge
    "doc.create": _cap(CapabilityFamily.DOC, False, "medium"),
    "doc.update": _cap(CapabilityFamily.DOC, False, "medium"),
    "doc.get": _cap(CapabilityFamily.DOC, True),
    "doc.search": _cap(CapabilityFamily.DOC, True),
    "doc.delete": _cap(CapabilityFamily.DOC, False, "critical"),
    "doc.comment": _cap(CapabilityFamily.DOC, False, "medium"),
    "doc.append": _cap(CapabilityFamily.DOC, False, "medium"),
    "doc.query": _cap(CapabilityFamily.DOC, True),
    # Workflow / project
    "workflow.create_issue": _cap(CapabilityFamily.WORKFLOW, False, "medium"),
    "workflow.update_issue": _cap(CapabilityFamily.WORKFLOW, False, "medium"),
    "workflow.transition": _cap(CapabilityFamily.WORKFLOW, False, "medium"),
    "workflow.comment": _cap(CapabilityFamily.WORKFLOW, False, "medium"),
    "workflow.list": _cap(CapabilityFamily.WORKFLOW, True),
    "workflow.get": _cap(CapabilityFamily.WORKFLOW, True),
    "workflow.search": _cap(CapabilityFamily.WORKFLOW, True),
    "workflow.delete": _cap(CapabilityFamily.WORKFLOW, False, "critical"),
    "workflow.get_teams": _cap(CapabilityFamily.WORKFLOW, True),
    # Messaging
    "messaging.send": _cap(CapabilityFamily.MESSAGING, False, "high"),
    "messaging.reply": _cap(CapabilityFamily.MESSAGING, False, "high"),
    "messaging.react": _cap(CapabilityFamily.MESSAGING, False, "medium"),
    "messaging.update": _cap(CapabilityFamily.MESSAGING, False, "medium"),
    "messaging.list_channels": _cap(CapabilityFamily.MESSAGING, True),
    "messaging.get_history": _cap(CapabilityFamily.MESSAGING, True),
    "messaging.get_thread": _cap(CapabilityFamily.MESSAGING, True),
    "messaging.get_users": _cap(CapabilityFamily.MESSAGING, True),
    "messaging.get_profile": _cap(CapabilityFamily.MESSAGING, True),
    "messaging.search": _cap(CapabilityFamily.MESSAGING, True),
    "messaging.send_template": _cap(CapabilityFamily.MESSAGING, False, "high"),
    "messaging.mark_read": _cap(CapabilityFamily.MESSAGING, False),
    # Browser
    "browser.open": _cap(CapabilityFamily.BROWSER, False, "medium"),
    "browser.snapshot": _cap(CapabilityFamily.BROWSER, True),
    "browser.extract": _cap(CapabilityFamily.BROWSER, True),
    "browser.click": _cap(CapabilityFamily.BROWSER, False, "medium"),
    "browser.type": _cap(CapabilityFamily.BROWSER, False, "medium"),
    "browser.submit": _cap(CapabilityFamily.BROWSER, False, "high"),
    "browser.screenshot": _cap(CapabilityFamily.BROWSER, True),
    # Search
    "search.web": _cap(CapabilityFamily.SEARCH, True),
    "search.memory": _cap(CapabilityFamily.SEARCH, True),
    "search.users": _cap(CapabilityFamily.SEARCH, True),
    "search.orgs": _cap(CapabilityFamily.SEARCH, True),
    # Social
    "messaging.post": _cap(CapabilityFamily.MESSAGING, False, "high"),
    "messaging.share": _cap(CapabilityFamily.MESSAGING, False, "high"),
    "messaging.get_mentions": _cap(CapabilityFamily.MESSAGING, True),
    # Internal intelligence
    "internal.search": _cap(CapabilityFamily.INTERNAL, True),
    "internal.get_plans": _cap(CapabilityFamily.INTERNAL, True),
    "internal.get_briefing": _cap(CapabilityFamily.INTERNAL, True),
    "internal.get_cursor": _cap(CapabilityFamily.INTERNAL, True),
    "internal.report_observation": _cap(CapabilityFamily.INTERNAL, False),
    "internal.get_task": _cap(CapabilityFamily.INTERNAL, True),
    "internal.get_goals": _cap(CapabilityFamily.INTERNAL, True),
    "internal.build_context": _cap(CapabilityFamily.INTERNAL, True),
    "internal.ingest_event": _cap(CapabilityFamily.INTERNAL, False),
    "internal.update_entity": _cap(CapabilityFamily.INTERNAL, False),
    "internal.plan_command": _cap(CapabilityFamily.INTERNAL, False),
    "internal.evaluate_policy": _cap(CapabilityFamily.INTERNAL, False),
    "internal.approve_action": _cap(CapabilityFamily.INTERNAL, False, "medium"),
    "internal.update_cursor": _cap(CapabilityFamily.INTERNAL, False),
    "internal.extract_preferences": _cap(CapabilityFamily.INTERNAL, False),
    "internal.create_task": _cap(CapabilityFamily.INTERNAL, False),
    "internal.verify_run": _cap(CapabilityFamily.INTERNAL, False),
    "internal.update_execution": _cap(CapabilityFamily.INTERNAL, False),
    "internal.push_ui": _cap(CapabilityFamily.INTERNAL, False),
    "internal.send_telegram": _cap(CapabilityFamily.MESSAGING, False, "medium"),
    "internal.send_approval": _cap(CapabilityFamily.MESSAGING, False, "medium"),
    # Drive
    "doc.drive_list": _cap(CapabilityFamily.DOC, True),
    "doc.drive_search": _cap(CapabilityFamily.DOC, True),
    "doc.drive_create": _cap(CapabilityFamily.DOC, False, "medium"),
    "doc.drive_delete": _cap(CapabilityFamily.DOC, False, "critical"),
}

# ── Tool name → capability mapping ──────────────────────────────────────────
# Maps every known tool name (native + MCP raw) to a canonical capability.

TOOL_TO_CAPABILITY: dict[str, str] = {
    # Gmail native
    "gmail_send": "email.send",
    "gmail_send_email": "email.send",
    "gmail_create_draft": "email.draft",
    "gmail_draft": "email.draft",
    "gmail_reply": "email.reply",
    "gmail_list": "email.list",
    "gmail_list_unread": "email.list",
    "gmail_read": "email.read",
    "gmail_get_message": "email.read",
    "gmail_search": "email.search",
    "gmail_delete": "email.delete",
    "gmail_archive": "email.delete",
    "gmail_mark_read": "email.read",
    # Gmail MCP (camelCase)
    "sendGmailDraft": "email.send",
    "createGmailDraft": "email.draft",
    "listGmailMessages": "email.list",
    "readGmailMessage": "email.read",
    "searchGmail": "email.search",
    "deleteGmailMessage": "email.delete",
    # Calendar native
    "calendar_list": "calendar.list",
    "calendar_get": "calendar.get",
    "calendar_create": "calendar.create",
    "calendar_create_event": "calendar.create",
    "calendar_update": "calendar.update",
    "calendar_update_event": "calendar.update",
    "calendar_delete": "calendar.delete",
    "calendar_delete_event": "calendar.delete",
    # Calendar MCP (camelCase)
    "createCalendarEvent": "calendar.create",
    "updateCalendarEvent": "calendar.update",
    "deleteCalendarEvent": "calendar.delete",
    "listCalendarEvents": "calendar.list",
    "getCalendarEvent": "calendar.get",
    # GitHub native
    "github_create_issue": "issue.create",
    "github_comment": "issue.comment",
    "github_create_pr": "repo.create_pr",
    "github_merge_pr": "repo.merge_pr",
    # GitHub MCP
    "issue_write": "issue.create",
    "issue_read": "issue.get",
    "add_issue_comment": "issue.comment",
    "create_pull_request": "repo.create_pr",
    "merge_pull_request": "repo.merge_pr",
    "update_pull_request": "repo.update_pr",
    "pull_request_read": "repo.list_prs",
    "pull_request_review_write": "repo.review_pr",
    "sub_issue_write": "issue.sub_issue",
    "list_issues": "issue.list",
    "search_issues": "issue.search",
    "search_code": "repo.search_code",
    "search_repositories": "repo.search_repos",
    "search_users": "search.users",
    "search_orgs": "search.orgs",
    "get_diff": "repo.get_diff",
    "get_reviews": "repo.get_reviews",
    "get_check_runs": "repo.get_checks",
    "get_files": "repo.get_files",
    "list_pull_requests": "repo.list_prs",
    "search_pull_requests": "repo.search_prs",
    "get_sub_issues": "issue.get",
    # Slack native
    "slack_send_message": "messaging.send",
    "slack_post_message": "messaging.send",
    "slack_react": "messaging.react",
    "slack_update_message": "messaging.update",
    "slack_list_channels": "messaging.list_channels",
    "slack_get_messages": "messaging.get_history",
    "slack_search": "messaging.search",
    # Slack MCP
    "slack_reply_to_thread": "messaging.reply",
    "slack_add_reaction": "messaging.react",
    "slack_get_channel_history": "messaging.get_history",
    "slack_get_thread_replies": "messaging.get_thread",
    "slack_get_users": "messaging.get_users",
    "slack_get_user_profile": "messaging.get_profile",
    # Linear native
    "linear_create_issue": "workflow.create_issue",
    "linear_update_issue": "workflow.update_issue",
    "linear_comment": "workflow.comment",
    "linear_list_issues": "workflow.list",
    "linear_get_issue": "workflow.get",
    # Linear MCP
    "linear_edit_issue": "workflow.update_issue",
    "linear_create_comment": "workflow.comment",
    "linear_delete_issue": "workflow.delete",
    "linear_search_issues": "workflow.search",
    "linear_get_teams": "workflow.get_teams",
    # Notion native
    "notion_create_page": "doc.create",
    "notion_update_page": "doc.update",
    "notion_search": "doc.search",
    "notion_get_page": "doc.get",
    # Notion MCP (kebab-case)
    "create-a-page": "doc.create",
    "update-a-page": "doc.update",
    "retrieve-a-page": "doc.get",
    "query-data-source": "doc.query",
    "create-a-comment": "doc.comment",
    "append-block-children": "doc.append",
    # Jira native
    "jira_create_issue": "issue.create",
    "jira_update_issue": "issue.update",
    "jira_transition": "issue.transition",
    "jira_comment": "issue.comment",
    "jira_list_issues": "issue.list",
    "jira_get_issue": "issue.get",
    # Jira MCP (camelCase)
    "getJiraIssue": "issue.get",
    "searchJiraIssuesUsingJql": "issue.search",
    "getVisibleJiraProjects": "issue.list",
    "getJiraIssueTypeMetaWithFields": "issue.get",
    "getJiraProjectIssueTypesMetadata": "issue.get",
    "getTransitionsForJiraIssue": "issue.get",
    "lookupJiraAccountId": "search.users",
    "getJiraIssueRemoteIssueLinks": "issue.get",
    "createJiraIssue": "issue.create",
    "editJiraIssue": "issue.update",
    "transitionJiraIssue": "issue.transition",
    "addCommentToJiraIssue": "issue.comment",
    "addWorklogToJiraIssue": "issue.update",
    # Drive
    "drive_list": "doc.drive_list",
    "drive_search": "doc.drive_search",
    "drive_create": "doc.drive_create",
    "drive_delete": "doc.drive_delete",
    # Browser (native module)
    "browser_open": "browser.open",
    "browser_snapshot": "browser.snapshot",
    "browser_extract": "browser.extract",
    "browser_click": "browser.click",
    "browser_type": "browser.type",
    "browser_submit": "browser.submit",
    "browser_screenshot": "browser.screenshot",
    # Browser (Playwright MCP — @playwright/mcp tool names)
    "browser_navigate": "browser.open",
    "browser_tabs": "browser.open",
    "browser_press_key": "browser.type",
    "browser_select_option": "browser.click",
    "browser_hover": "browser.click",
    "browser_drag": "browser.click",
    "browser_handle_dialog": "browser.click",
    "browser_file_upload": "browser.submit",
    "browser_wait": "browser.snapshot",
    "browser_close": "browser.open",
    "browser_resize": "browser.open",
    "browser_pdf_save": "browser.screenshot",
    "browser_network_requests": "browser.snapshot",
    "browser_console_messages": "browser.snapshot",
    # Research
    "web_search": "search.web",
    "perplexity_search": "search.web",
    # Internal intelligence
    "search": "internal.search",
    "intelligence_search": "internal.search",
    "get_active_plans": "internal.get_plans",
    "get_briefing": "internal.get_briefing",
    "get_observation_cursor": "internal.get_cursor",
    "report_observation": "internal.report_observation",
    "get_task": "internal.get_task",
    "get_goals": "internal.get_goals",
    "build_context": "internal.build_context",
    "ingest_event": "internal.ingest_event",
    "update_entity": "internal.update_entity",
    "plan_command": "internal.plan_command",
    "evaluate_policy": "internal.evaluate_policy",
    "approve_action": "internal.approve_action",
    "update_observation_cursor": "internal.update_cursor",
    "extract_preferences": "internal.extract_preferences",
    "create_task": "internal.create_task",
    "verify_run": "internal.verify_run",
    "update_execution": "internal.update_execution",
    "push_ui_update": "internal.push_ui",
    "send_telegram": "internal.send_telegram",
    "send_approval_prompt": "internal.send_approval",
    # WhatsApp
    "whatsapp_send_message": "messaging.send",
    "whatsapp_send_template": "messaging.send_template",
    "whatsapp_mark_read": "messaging.mark_read",
    # SMS
    "sms_send_sms": "messaging.send",
    # LinkedIn
    "linkedin_create_post": "messaging.post",
    "linkedin_share_article": "messaging.share",
    "linkedin_get_profile": "messaging.get_profile",
    # Twitter
    "twitter_create_tweet": "messaging.post",
    "twitter_reply": "messaging.reply",
    "twitter_retweet": "messaging.share",
    "twitter_get_mentions": "messaging.get_mentions",
}


def get_capability_for_tool(tool_name: str) -> str | None:
    """Look up the canonical capability for a tool name."""
    return TOOL_TO_CAPABILITY.get(tool_name)


def get_family_for_capability(capability: str) -> CapabilityFamily | None:
    """Extract the family from a capability string."""
    meta = CAPABILITY_CATALOG.get(capability)
    if meta:
        return meta.family
    prefix = capability.split(".")[0] if "." in capability else None
    if prefix:
        try:
            return CapabilityFamily(prefix)
        except ValueError:
            pass
    return None


def is_read_only_capability(capability: str) -> bool:
    """Check if a capability is read-only."""
    meta = CAPABILITY_CATALOG.get(capability)
    return meta.read_only if meta else False
