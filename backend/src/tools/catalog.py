"""Internal tool catalog — single source of truth for internal tool definitions.

This module defines all internal (Jarvis-owned) MCP tools, their input schemas,
capabilities, risk levels, and metadata. Serves as a parallel registry during
the Unified Tool Registry migration (Phase 6).

Tools are organized by server:
- intelligence: 17 tools (search, ingest, policies, context, briefing, store_memory, etc.)
- communication: 3 tools (telegram, approval prompts, UI updates)
- _special: 1 tool (report_governor_verdict — inline-dispatched, not MCP)
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel

from src.tools.schemas import (
    ApproveActionInput,
    BuildContextInput,
    EvaluatePolicyInput,
    ExtractPreferencesInput,
    GetActivePlansInput,
    GetBriefingInput,
    GetGoalMemoriesInput,
    GetObservationCursorInput,
    GetPlanDetailsInput,
    IngestEventInput,
    PushUiUpdateInput,
    ReportGovernorVerdictInput,
    ReportObservationInput,
    SearchInput,
    SendApprovalPromptInput,
    SendTelegramInput,
    StoreMemoryInput,
    StorePreferenceInput,
    UpdateEntityInput,
    UpdateExecutionInput,
    UpdateObservationCursorInput,
    VerifyRunInput,
)


@dataclass(frozen=True, slots=True)
class InternalToolDef:
    """Internal tool definition with metadata."""

    name: str
    input_model: type[BaseModel]
    capability: str
    risk_level: str = "low"
    requires_approval: bool = False
    server: str = "intelligence"
    description: str = ""
    read_only: bool = False


# ── Internal Tool Registry ─────────────────────────────────────────


def _desc(model_cls: type[BaseModel]) -> str:
    """Extract description from model docstring."""
    return model_cls.__doc__.strip() if model_cls.__doc__ else ""


INTERNAL_TOOLS: list[InternalToolDef] = [
    # Intelligence server tools (17 tools)
    InternalToolDef(
        name="ingest_event",
        input_model=IngestEventInput,
        capability="internal.ingest_event",
        risk_level="low",
        requires_approval=False,
        server="intelligence",
        description=_desc(IngestEventInput),
        read_only=False,
    ),
    InternalToolDef(
        name="search",
        input_model=SearchInput,
        capability="internal.search",
        risk_level="low",
        requires_approval=False,
        server="intelligence",
        description=_desc(SearchInput),
        read_only=True,
    ),
    InternalToolDef(
        name="evaluate_policy",
        input_model=EvaluatePolicyInput,
        capability="internal.evaluate_policy",
        risk_level="low",
        requires_approval=False,
        server="intelligence",
        description=_desc(EvaluatePolicyInput),
        read_only=True,
    ),
    InternalToolDef(
        name="get_briefing",
        input_model=GetBriefingInput,
        capability="internal.get_briefing",
        risk_level="low",
        requires_approval=False,
        server="intelligence",
        description=_desc(GetBriefingInput),
        read_only=True,
    ),
    InternalToolDef(
        name="get_observation_cursor",
        input_model=GetObservationCursorInput,
        capability="internal.get_cursor",
        risk_level="low",
        requires_approval=False,
        server="intelligence",
        description=_desc(GetObservationCursorInput),
        read_only=True,
    ),
    InternalToolDef(
        name="update_observation_cursor",
        input_model=UpdateObservationCursorInput,
        capability="internal.update_cursor",
        risk_level="low",
        requires_approval=False,
        server="intelligence",
        description=_desc(UpdateObservationCursorInput),
        read_only=False,
    ),
    InternalToolDef(
        name="report_observation",
        input_model=ReportObservationInput,
        capability="internal.report_observation",
        risk_level="low",
        requires_approval=False,
        server="intelligence",
        description=_desc(ReportObservationInput),
        read_only=False,
    ),
    InternalToolDef(
        name="approve_action",
        input_model=ApproveActionInput,
        capability="internal.approve_action",
        risk_level="medium",
        requires_approval=True,
        server="intelligence",
        description=_desc(ApproveActionInput),
        read_only=False,
    ),
    InternalToolDef(
        name="update_execution",
        input_model=UpdateExecutionInput,
        capability="internal.update_execution",
        risk_level="low",
        requires_approval=False,
        server="intelligence",
        description=_desc(UpdateExecutionInput),
        read_only=False,
    ),
    InternalToolDef(
        name="update_entity",
        input_model=UpdateEntityInput,
        capability="internal.update_entity",
        risk_level="low",
        requires_approval=False,
        server="intelligence",
        description=_desc(UpdateEntityInput),
        read_only=False,
    ),
    InternalToolDef(
        name="get_active_plans",
        input_model=GetActivePlansInput,
        capability="internal.get_plans",
        risk_level="low",
        requires_approval=False,
        server="intelligence",
        description=_desc(GetActivePlansInput),
        read_only=True,
    ),
    InternalToolDef(
        name="extract_preferences",
        input_model=ExtractPreferencesInput,
        capability="internal.extract_preferences",
        risk_level="low",
        requires_approval=False,
        server="intelligence",
        description=_desc(ExtractPreferencesInput),
        read_only=False,
    ),
    InternalToolDef(
        name="get_goal_memories",
        input_model=GetGoalMemoriesInput,
        capability="internal.get_goals",
        risk_level="low",
        requires_approval=False,
        server="intelligence",
        description=_desc(GetGoalMemoriesInput),
        read_only=True,
    ),
    InternalToolDef(
        name="build_context",
        input_model=BuildContextInput,
        capability="internal.build_context",
        risk_level="low",
        requires_approval=False,
        server="intelligence",
        description=_desc(BuildContextInput),
        read_only=True,
    ),
    InternalToolDef(
        name="verify_run",
        input_model=VerifyRunInput,
        capability="internal.verify_run",
        risk_level="low",
        requires_approval=False,
        server="intelligence",
        description=_desc(VerifyRunInput),
        read_only=True,
    ),
    InternalToolDef(
        name="store_memory",
        input_model=StoreMemoryInput,
        capability="internal.store_memory",
        risk_level="low",
        requires_approval=False,
        server="intelligence",
        description=_desc(StoreMemoryInput),
        read_only=False,
    ),
    InternalToolDef(
        name="store_preference",
        input_model=StorePreferenceInput,
        capability="internal.store_preference",
        risk_level="low",
        requires_approval=False,
        server="intelligence",
        description=_desc(StorePreferenceInput),
        read_only=False,
    ),
    InternalToolDef(
        name="get_plan_details",
        input_model=GetPlanDetailsInput,
        capability="internal.get_plan_details",
        risk_level="low",
        requires_approval=False,
        server="intelligence",
        description=_desc(GetPlanDetailsInput),
        read_only=True,
    ),
    # Special: inline-dispatched (not a real MCP tool).
    # Shares capability with evaluate_policy — both are governor-domain tools.
    # A dedicated capability would require modifying capabilities.py (out of scope
    # for Phase 6). Phase 10 startup validation can revisit if needed.
    InternalToolDef(
        name="report_governor_verdict",
        input_model=ReportGovernorVerdictInput,
        capability="internal.evaluate_policy",
        risk_level="low",
        requires_approval=False,
        server="_special",
        description=_desc(ReportGovernorVerdictInput),
        read_only=False,
    ),
    # Communication server tools (3 tools)
    InternalToolDef(
        name="send_telegram",
        input_model=SendTelegramInput,
        capability="internal.send_telegram",
        risk_level="medium",
        requires_approval=True,
        server="communication",
        description=_desc(SendTelegramInput),
        read_only=False,
    ),
    InternalToolDef(
        name="send_approval_prompt",
        input_model=SendApprovalPromptInput,
        capability="internal.send_approval",
        risk_level="medium",
        requires_approval=True,
        server="communication",
        description=_desc(SendApprovalPromptInput),
        read_only=False,
    ),
    InternalToolDef(
        name="push_ui_update",
        input_model=PushUiUpdateInput,
        capability="internal.push_ui",
        risk_level="low",
        requires_approval=False,
        server="communication",
        description=_desc(PushUiUpdateInput),
        read_only=False,
    ),
]


# ── Helper Functions ───────────────────────────────────────────────


def get_internal_tool_names() -> set[str]:
    """Return set of all internal tool names."""
    return {tool.name for tool in INTERNAL_TOOLS}


def get_internal_tool_by_name(name: str) -> InternalToolDef | None:
    """Lookup internal tool by name. Returns None if not found."""
    for tool in INTERNAL_TOOLS:
        if tool.name == name:
            return tool
    return None


def get_internal_tools_for_server(server: str) -> list[InternalToolDef]:
    """Filter internal tools by server. Returns empty list if no matches."""
    return [tool for tool in INTERNAL_TOOLS if tool.server == server]


# ── External Tool Registry ─────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ExternalToolSeed:
    """External tool seed definition for MCP tools."""

    name: str
    capability: str
    server: str
    risk_level: str = "medium"
    requires_approval: bool = True
    verified: bool = False


def _ext(
    name: str,
    cap: str,
    server: str,
    risk: str = "medium",
    approval: bool = True,
    verified: bool = False,
) -> ExternalToolSeed:
    """Helper to create ExternalToolSeed with compact syntax."""
    return ExternalToolSeed(
        name=name,
        capability=cap,
        server=server,
        risk_level=risk,
        requires_approval=approval,
        verified=verified,
    )


# Per-tool risk_level may intentionally differ from the capability-level risk in
# CAPABILITY_CATALOG. Example: browser_tabs maps to browser.open (medium) but is
# itself low-risk. Tool-granularity risk is more accurate than capability-granularity.
EXTERNAL_TOOL_SEEDS: list[ExternalToolSeed] = [
    # _ext(name, capability, server, risk, approval, verified)
    # google-workspace (18 tools, verified=True)
    # Complete tier, gmail + calendar. Real names confirmed via list_tools() 2026-03-30.
    _ext("search_gmail_messages", "email.search", "google-workspace", "low", False, True),
    _ext("get_gmail_message_content", "email.read", "google-workspace", "low", False, True),
    _ext("get_gmail_messages_content_batch", "email.read", "google-workspace", "low", False, True),
    _ext("send_gmail_message", "email.send", "google-workspace", "high", True, True),
    _ext("draft_gmail_message", "email.draft", "google-workspace", "medium", True, True),
    _ext("modify_gmail_message_labels", "email.send", "google-workspace", "medium", True, True),
    _ext(
        "batch_modify_gmail_message_labels", "email.send", "google-workspace", "medium", True, True
    ),
    _ext("get_gmail_thread_content", "email.read", "google-workspace", "low", False, True),
    _ext("get_gmail_threads_content_batch", "email.read", "google-workspace", "low", False, True),
    _ext("get_gmail_attachment_content", "email.read", "google-workspace", "low", False, True),
    _ext("list_gmail_labels", "email.list", "google-workspace", "low", False, True),
    _ext("list_gmail_filters", "email.list", "google-workspace", "low", False, True),
    _ext("manage_gmail_filter", "email.send", "google-workspace", "medium", True, True),
    _ext("manage_gmail_label", "email.send", "google-workspace", "medium", True, True),
    _ext("get_events", "calendar.list", "google-workspace", "low", False, True),
    _ext("list_calendars", "calendar.list", "google-workspace", "low", False, True),
    _ext("manage_event", "calendar.create", "google-workspace", "medium", True, True),
    _ext("query_freebusy", "calendar.get", "google-workspace", "low", False, True),
    # github (22 tools, verified=False)
    _ext("issue_write", "issue.create", "github", "medium", True, False),
    _ext("issue_read", "issue.get", "github", "low", False, False),
    _ext("add_issue_comment", "issue.comment", "github", "medium", True, False),
    _ext("create_pull_request", "repo.create_pr", "github", "high", True, False),
    _ext("merge_pull_request", "repo.merge_pr", "github", "high", True, False),
    _ext("update_pull_request", "repo.update_pr", "github", "medium", True, False),
    _ext("pull_request_read", "repo.list_prs", "github", "low", False, False),
    _ext("pull_request_review_write", "repo.review_pr", "github", "medium", True, False),
    _ext("sub_issue_write", "issue.sub_issue", "github", "medium", True, False),
    _ext("list_issues", "issue.list", "github", "low", False, False),
    _ext("search_issues", "issue.search", "github", "low", False, False),
    _ext("search_code", "repo.search_code", "github", "low", False, False),
    _ext("search_repositories", "repo.search_repos", "github", "low", False, False),
    _ext("search_users", "search.users", "github", "low", False, False),
    _ext("search_orgs", "search.orgs", "github", "low", False, False),
    _ext("get_diff", "repo.get_diff", "github", "low", False, False),
    _ext("get_reviews", "repo.get_reviews", "github", "low", False, False),
    _ext("get_check_runs", "repo.get_checks", "github", "low", False, False),
    _ext("get_files", "repo.get_files", "github", "low", False, False),
    _ext("list_pull_requests", "repo.list_prs", "github", "low", False, False),
    _ext("search_pull_requests", "repo.search_prs", "github", "low", False, False),
    _ext("get_sub_issues", "issue.get", "github", "low", False, False),
    # slack (8 tools, verified=False)
    _ext("slack_post_message", "messaging.send", "slack", "high", True, False),
    _ext("slack_reply_to_thread", "messaging.reply", "slack", "high", True, False),
    _ext("slack_add_reaction", "messaging.react", "slack", "medium", True, False),
    _ext("slack_get_channel_history", "messaging.get_history", "slack", "low", False, False),
    _ext("slack_get_thread_replies", "messaging.get_thread", "slack", "low", False, False),
    _ext("slack_get_users", "messaging.get_users", "slack", "low", False, False),
    _ext("slack_get_user_profile", "messaging.get_profile", "slack", "low", False, False),
    _ext("slack_list_channels", "messaging.list_channels", "slack", "low", False, False),
    # notion (22 tools, verified=True)
    _ext("API-post-page", "doc.create", "notion", "medium", True, True),
    _ext("API-patch-page", "doc.update", "notion", "medium", True, True),
    _ext("API-retrieve-a-page", "doc.get", "notion", "low", False, True),
    _ext("API-query-data-source", "doc.query", "notion", "low", False, True),
    _ext("API-create-a-comment", "doc.comment", "notion", "medium", True, True),
    _ext("API-patch-block-children", "doc.append", "notion", "medium", True, True),
    _ext("API-retrieve-a-page-property", "doc.get_property", "notion", "low", False, True),
    _ext("API-retrieve-a-comment", "doc.get_comment", "notion", "low", False, True),
    _ext("API-get-block-children", "doc.get_children", "notion", "low", False, True),
    _ext("API-retrieve-a-block", "doc.get_block", "notion", "low", False, True),
    _ext("API-update-a-block", "doc.update_block", "notion", "medium", True, True),
    _ext("API-delete-a-block", "doc.delete_block", "notion", "high", True, True),
    _ext("API-move-page", "doc.move", "notion", "medium", True, True),
    _ext("API-retrieve-a-database", "doc.get_database", "notion", "low", False, True),
    _ext("API-create-a-data-source", "doc.create_datasource", "notion", "medium", True, True),
    _ext("API-retrieve-a-data-source", "doc.get_datasource", "notion", "low", False, True),
    _ext("API-update-a-data-source", "doc.update_datasource", "notion", "medium", True, True),
    _ext("API-list-data-source-templates", "doc.list_templates", "notion", "low", False, True),
    _ext("API-get-self", "doc.get_self", "notion", "low", False, True),
    _ext("API-get-user", "doc.get_user", "notion", "low", False, True),
    _ext("API-get-users", "doc.get_users", "notion", "low", False, True),
    _ext("API-post-search", "doc.search", "notion", "low", False, True),
    # linear (24 tools, verified=True)
    _ext("linear_create_issue", "workflow.create_issue", "linear", "medium", True, True),
    _ext("linear_get_issue", "workflow.get", "linear", "low", False, True),
    _ext("linear_edit_issue", "workflow.update_issue", "linear", "medium", True, True),
    _ext("linear_create_comment", "workflow.comment", "linear", "medium", True, True),
    _ext("linear_delete_issue", "workflow.delete", "linear", "critical", True, True),
    _ext("linear_search_issues", "workflow.search", "linear", "low", False, True),
    _ext("linear_get_teams", "workflow.get_teams", "linear", "low", False, True),
    _ext("linear_create_issues", "workflow.create_issues", "linear", "medium", True, True),
    _ext("linear_bulk_update_issues", "workflow.bulk_update", "linear", "medium", True, True),
    _ext(
        "linear_search_issues_by_identifier", "workflow.search_by_id", "linear", "low", False, True
    ),
    _ext("linear_update_comment", "workflow.update_comment", "linear", "medium", True, True),
    _ext("linear_delete_comment", "workflow.delete_comment", "linear", "high", True, True),
    _ext("linear_resolve_comment", "workflow.resolve_comment", "linear", "medium", True, True),
    _ext("linear_unresolve_comment", "workflow.unresolve_comment", "linear", "medium", True, True),
    _ext("linear_get_user", "workflow.get_user", "linear", "low", False, True),
    _ext("linear_get_project", "workflow.get_project", "linear", "low", False, True),
    _ext("linear_list_projects", "workflow.list_projects", "linear", "low", False, True),
    _ext(
        "linear_create_project_with_issues",
        "workflow.create_project",
        "linear",
        "medium",
        True,
        True,
    ),
    _ext(
        "linear_create_project_milestone",
        "workflow.create_milestone",
        "linear",
        "medium",
        True,
        True,
    ),
    _ext("linear_get_project_milestones", "workflow.get_milestones", "linear", "low", False, True),
    _ext(
        "linear_update_project_milestone",
        "workflow.update_milestone",
        "linear",
        "medium",
        True,
        True,
    ),
    _ext(
        "linear_delete_project_milestone", "workflow.delete_milestone", "linear", "high", True, True
    ),
    _ext(
        "linear_create_customer_need_from_attachment",
        "workflow.create_customer_need",
        "linear",
        "medium",
        True,
        True,
    ),
    _ext("linear_auth_callback", "workflow.auth", "linear", "low", False, True),
    # playwright (22 tools, verified=True)
    _ext("browser_navigate", "browser.open", "playwright", "medium", False, True),
    _ext("browser_snapshot", "browser.snapshot", "playwright", "low", False, True),
    _ext("browser_click", "browser.click", "playwright", "medium", False, True),
    _ext("browser_type", "browser.type", "playwright", "medium", False, True),
    _ext("browser_tabs", "browser.open", "playwright", "low", False, True),
    _ext("browser_press_key", "browser.type", "playwright", "medium", False, True),
    _ext("browser_select_option", "browser.click", "playwright", "medium", False, True),
    _ext("browser_hover", "browser.click", "playwright", "medium", False, True),
    _ext("browser_drag", "browser.click", "playwright", "medium", False, True),
    _ext("browser_handle_dialog", "browser.click", "playwright", "medium", False, True),
    _ext("browser_file_upload", "browser.submit", "playwright", "high", True, True),
    _ext("browser_close", "browser.open", "playwright", "low", False, True),
    _ext("browser_resize", "browser.open", "playwright", "low", False, True),
    _ext("browser_network_requests", "browser.snapshot", "playwright", "low", False, True),
    _ext("browser_console_messages", "browser.snapshot", "playwright", "low", False, True),
    _ext("browser_evaluate", "browser.execute", "playwright", "high", True, True),
    _ext("browser_run_code", "browser.execute", "playwright", "high", True, True),
    _ext("browser_install", "browser.install", "playwright", "medium", False, True),
    _ext("browser_navigate_back", "browser.navigate_back", "playwright", "low", False, True),
    _ext("browser_take_screenshot", "browser.screenshot", "playwright", "low", False, True),
    _ext("browser_wait_for", "browser.wait", "playwright", "low", False, True),
    _ext("browser_fill_form", "browser.type", "playwright", "medium", False, True),
    # filesystem (14 tools, verified=True)
    _ext("read_text_file", "filesystem.read", "filesystem", "low", False, True),
    _ext("read_file", "filesystem.read", "filesystem", "low", False, True),
    _ext("read_media_file", "filesystem.read_media", "filesystem", "low", False, True),
    _ext("read_multiple_files", "filesystem.read", "filesystem", "low", False, True),
    _ext("write_file", "filesystem.write", "filesystem", "high", True, True),
    _ext("edit_file", "filesystem.write", "filesystem", "high", True, True),
    _ext("create_directory", "filesystem.write", "filesystem", "medium", True, True),
    _ext("move_file", "filesystem.move", "filesystem", "high", True, True),
    _ext("list_directory", "filesystem.list", "filesystem", "low", False, True),
    _ext("list_directory_with_sizes", "filesystem.list", "filesystem", "low", False, True),
    _ext("directory_tree", "filesystem.list", "filesystem", "low", False, True),
    _ext("get_file_info", "filesystem.read", "filesystem", "low", False, True),
    _ext("search_files", "filesystem.search", "filesystem", "low", False, True),
    _ext("list_allowed_directories", "filesystem.list", "filesystem", "low", False, True),
    # atlassian (13 tools, verified=False)
    _ext("getJiraIssue", "issue.get", "atlassian", "low", False, False),
    _ext("searchJiraIssuesUsingJql", "issue.search", "atlassian", "low", False, False),
    _ext("getVisibleJiraProjects", "issue.list", "atlassian", "low", False, False),
    _ext("getJiraIssueTypeMetaWithFields", "issue.get", "atlassian", "low", False, False),
    _ext("getJiraProjectIssueTypesMetadata", "issue.get", "atlassian", "low", False, False),
    _ext("getTransitionsForJiraIssue", "issue.get", "atlassian", "low", False, False),
    _ext("lookupJiraAccountId", "search.users", "atlassian", "low", False, False),
    _ext("getJiraIssueRemoteIssueLinks", "issue.get", "atlassian", "low", False, False),
    _ext("createJiraIssue", "issue.create", "atlassian", "medium", True, False),
    _ext("editJiraIssue", "issue.update", "atlassian", "medium", True, False),
    _ext("transitionJiraIssue", "issue.transition", "atlassian", "medium", True, False),
    _ext("addCommentToJiraIssue", "issue.comment", "atlassian", "medium", True, False),
    _ext("addWorklogToJiraIssue", "issue.update", "atlassian", "medium", True, False),
    # _composite (1 tool, verified=False)
    _ext("web_search", "search.web", "_composite", "low", False, False),
]


# ── External Tool Helper Functions ─────────────────────────────────


def get_seeds_for_server(server: str) -> list[ExternalToolSeed]:
    """Filter external tool seeds by server. Returns empty list if no matches."""
    return [seed for seed in EXTERNAL_TOOL_SEEDS if seed.server == server]


def get_verified_seeds() -> list[ExternalToolSeed]:
    """Return only verified external tool seeds."""
    return [seed for seed in EXTERNAL_TOOL_SEEDS if seed.verified]
