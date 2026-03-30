"""Internal tool catalog — single source of truth for internal tool definitions.

This module defines all internal (Jarvis-owned) MCP tools, their input schemas,
capabilities, risk levels, and metadata. Serves as a parallel registry during
the Unified Tool Registry migration (Phase 6).

Tools are organized by server:
- intelligence: 15 tools (search, ingest, policies, context, briefing, etc.)
- communication: 3 tools (telegram, approval prompts, UI updates)
- _special: 1 tool (report_governor_verdict — inline-dispatched, not MCP)
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel

from src.orchestrator.tool_schemas import (
    ApproveActionInput,
    BuildContextInput,
    EvaluatePolicyInput,
    ExtractPreferencesInput,
    GetActivePlansInput,
    GetBriefingInput,
    GetGoalMemoriesInput,
    GetObservationCursorInput,
    IngestEventInput,
    PushUiUpdateInput,
    ReportGovernorVerdictInput,
    ReportObservationInput,
    SearchInput,
    SendApprovalPromptInput,
    SendTelegramInput,
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
    # Intelligence server tools (15 tools)
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
