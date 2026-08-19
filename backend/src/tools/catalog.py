"""Internal tool catalog — single source of truth for internal tool definitions.

This module defines all internal (Muldro-owned) MCP tools, their input schemas,
capabilities, risk levels, and metadata. Serves as a parallel registry during
the Unified Tool Registry migration (Phase 6).

Tools are organized by server:
- intelligence: 19 tools (search, ingest, policies, context, briefing, etc.)
- communication: 2 tools (UI updates, rich surfaces)
- _special: 1 tool (report_governor_verdict — inline-dispatched, not MCP)
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel

from src.integrations.gateway_actions import PROVIDER_REGISTRY
from src.integrations.gateway_naming import action_id_to_tool_name
from src.tools.schemas import (
    AddToBriefInput,
    ApproveActionInput,
    BuildContextInput,
    DiscoverCapabilitiesInput,
    EvaluatePolicyInput,
    ExtractPreferencesInput,
    GetActivePlansInput,
    GetBriefingInput,
    GetEntityInput,
    GetGoalMemoriesInput,
    GetObservationCursorInput,
    GetPlanDetailsInput,
    GetProvenanceInput,
    IngestEventInput,
    PushUiUpdateInput,
    QueryFactsInput,
    RenderSurfaceInput,
    ReportGovernorVerdictInput,
    ReportObservationInput,
    ScheduleReminderInput,
    SearchInput,
    SetGoalInput,
    SetInstructionInput,
    StoreMemoryInput,
    StorePreferenceInput,
    TraverseInput,
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
    # Intelligence server tools (19 tools)
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
    InternalToolDef(
        name="discover_capabilities",
        input_model=DiscoverCapabilitiesInput,
        capability="system.discovery",
        risk_level="none",
        requires_approval=False,
        server="intelligence",
        description=_desc(DiscoverCapabilitiesInput),
        read_only=True,
    ),
    # World-model read tools (spec §4.6 item 5) — workspace-filtered fail-closed reads.
    InternalToolDef(
        name="get_entity",
        input_model=GetEntityInput,
        capability="internal.get_entity",
        risk_level="low",
        requires_approval=False,
        server="intelligence",
        description=_desc(GetEntityInput),
        read_only=True,
    ),
    InternalToolDef(
        name="query_facts",
        input_model=QueryFactsInput,
        capability="internal.query_facts",
        risk_level="low",
        requires_approval=False,
        server="intelligence",
        description=_desc(QueryFactsInput),
        read_only=True,
    ),
    InternalToolDef(
        name="traverse",
        input_model=TraverseInput,
        capability="internal.traverse",
        risk_level="low",
        requires_approval=False,
        server="intelligence",
        description=_desc(TraverseInput),
        read_only=True,
    ),
    InternalToolDef(
        name="get_provenance",
        input_model=GetProvenanceInput,
        capability="internal.get_provenance",
        risk_level="low",
        requires_approval=False,
        server="intelligence",
        description=_desc(GetProvenanceInput),
        read_only=True,
    ),
    # System action tools (P2.5a) — the 4 promoted system.* capabilities. Writes into the
    # user's own data layer (goals / instructions / reminders / briefing); ALWAYS-ALLOWED on
    # the chat path (permission_gate + write_lock exempt, D5). Not held by any agent scope
    # yet (surfaced only once P2.5c wires the planless lead) — orphaned-but-harmless.
    InternalToolDef(
        name="set_goal",
        input_model=SetGoalInput,
        capability="system.set_goal",
        risk_level="low",
        requires_approval=False,
        server="intelligence",
        description=_desc(SetGoalInput),
        read_only=False,
    ),
    InternalToolDef(
        name="set_instruction",
        input_model=SetInstructionInput,
        capability="system.set_instruction",
        risk_level="low",
        requires_approval=False,
        server="intelligence",
        description=_desc(SetInstructionInput),
        read_only=False,
    ),
    InternalToolDef(
        name="schedule_reminder",
        input_model=ScheduleReminderInput,
        capability="system.schedule_reminder",
        risk_level="low",
        requires_approval=False,
        server="intelligence",
        description=_desc(ScheduleReminderInput),
        read_only=False,
    ),
    InternalToolDef(
        name="add_to_brief",
        input_model=AddToBriefInput,
        capability="system.add_to_brief",
        risk_level="low",
        requires_approval=False,
        server="intelligence",
        description=_desc(AddToBriefInput),
        read_only=False,
    ),
    # Special: inline-dispatched (not a real MCP tool). Has its own capability
    # (internal.report_verdict) so the tool↔capability mapping stays 1:1 — distinct
    # from evaluate_policy's internal.evaluate_policy. Both are governor-domain caps,
    # currently held by no agent scope (orphaned-but-harmless; validate_registry
    # tolerates capabilities without an agent holder).
    InternalToolDef(
        name="report_governor_verdict",
        input_model=ReportGovernorVerdictInput,
        capability="internal.report_verdict",
        risk_level="low",
        requires_approval=False,
        server="_special",
        description=_desc(ReportGovernorVerdictInput),
        read_only=False,
    ),
    # Communication server tools (2 tools)
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
    InternalToolDef(
        name="render_surface",
        input_model=RenderSurfaceInput,
        capability="internal.render_surface",
        risk_level="none",
        requires_approval=False,
        server="communication",
        description=_desc(RenderSurfaceInput),
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
    # google-workspace and github are gateway-only (OpenConnector) -- see the
    # derived block below EXTERNAL_TOOL_SEEDS. Native hand-written seeds for
    # these two migrated servers are deliberately absent.
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

# Gateway-backed servers -- DERIVED from the single source of truth
# (gateway_actions.PROVIDER_REGISTRY) with the agent-legal naming contract
# (dots are illegal in Anthropic/OpenAI tool names). Names MUST match what the
# adapter warm-start exposes (action_id_to_tool_name). Native hand-written seeds
# for these servers are deliberately absent: a migrated server is gateway-only.
EXTERNAL_TOOL_SEEDS += [
    _ext(
        action_id_to_tool_name(action.action_id),
        action.capability,
        provider.server_name,
        action.risk,
        action.requires_approval,
        True,
    )
    for provider in PROVIDER_REGISTRY.values()
    for action in provider.actions
]


# ── External Tool Helper Functions ─────────────────────────────────


def get_seeds_for_server(server: str) -> list[ExternalToolSeed]:
    """Filter external tool seeds by server. Returns empty list if no matches."""
    return [seed for seed in EXTERNAL_TOOL_SEEDS if seed.server == server]


def get_verified_seeds() -> list[ExternalToolSeed]:
    """Return only verified external tool seeds."""
    return [seed for seed in EXTERNAL_TOOL_SEEDS if seed.verified]
