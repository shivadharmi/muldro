"""Auto-generated tool schemas from Pydantic models.

Each tool input is a Pydantic BaseModel with Field descriptions.
Schemas are generated via .model_json_schema() — single source of truth.
"""

from typing import Literal

from pydantic import BaseModel, Field

# ── Tool Input Models ──────────────────────────────────────────────


class IngestEventInput(BaseModel):
    """Ingest an event into the Jarvis intelligence pipeline."""

    source: str = Field(description="Event source: gmail, calendar, slack, github, manual")
    event_type: str = Field(description="Type of event: message, meeting, commit, mention, etc.")
    entity_type: str = Field(description="Primary entity type: person, project, company, task")
    entity_id: str = Field(description="Unique identifier for the primary entity")
    title: str = Field(description="Human-readable event title")
    summary: str = Field(default="", description="Brief event summary")
    actor_email: str = Field(default="", description="Email of the person who triggered the event")
    actor_name: str = Field(default="", description="Display name of the actor")
    occurred_at: str = Field(default="", description="ISO 8601 timestamp when the event occurred")
    raw_payload: str = Field(
        default="", description="JSON string of raw source payload for archival"
    )


class SearchInput(BaseModel):
    """Unified search across all knowledge: memories, entities, events via TriSearch."""

    query: str = Field(description="Natural language search query")
    types: str = Field(
        default="",
        description="Comma-separated result type filter (e.g., 'memory,entity'). Empty = all.",
    )
    limit: int = Field(default=20, ge=1, le=100, description="Maximum results")


class EvaluatePolicyInput(BaseModel):
    """Evaluate governance policy for a plan.

    Returns one of: auto_execute (safe), approval_required (needs user OK),
    or blocked (dangerous operation).
    """

    plan_id: str = Field(description="ID of the plan to evaluate")


class GetBriefingInput(BaseModel):
    """Generate or fetch the daily briefing."""

    date: str = Field(default="today", description="Date for briefing: 'today' or ISO date")


class GetObservationCursorInput(BaseModel):
    """Get the last observation checkpoint for a source."""

    source: str = Field(description="Data source name: gmail, calendar, slack, github")


class UpdateObservationCursorInput(BaseModel):
    """Update observation checkpoint after successful observation."""

    source: str = Field(description="Data source name")
    cursor_type: str = Field(description="Cursor type: timestamp, page_token, message_id")
    cursor_value: str = Field(description="New cursor value")


class ReportObservationInput(BaseModel):
    """Report observation cycle results for health tracking."""

    source: str = Field(description="Data source name")
    items_found: int = Field(default=0, description="Number of items found in this cycle")
    items_ingested: int = Field(default=0, description="Number of items ingested")
    status: str = Field(default="ok", description="Cycle status: ok, partial, error")
    error_message: str = Field(default="", description="Error details if status is error")


class ApproveActionInput(BaseModel):
    """Approve or reject a pending action."""

    approval_id: str = Field(description="ID of the pending approval")
    decision: Literal["approved", "rejected"] = Field(description="Approval decision")
    reason: str = Field(default="", description="Reason for the decision")


class UpdateExecutionInput(BaseModel):
    """Update the status of an execution."""

    execution_id: str = Field(description="ID of the execution to update")
    status: str = Field(description="New status: running, completed, failed, cancelled")
    result_summary: str = Field(default="", description="Summary of execution result")
    error_message: str = Field(default="", description="Error details if failed")


class UpdateEntityInput(BaseModel):
    """Update an entity's attributes or add an alias."""

    entity_id: str = Field(description="ID of the entity to update")
    attributes: str = Field(default="", description="JSON string of attributes to set/update")
    add_alias: str = Field(default="", description="New alias to add to this entity")


class GetActivePlansInput(BaseModel):
    """Get currently active plans."""

    limit: int = Field(default=10, ge=1, le=50, description="Maximum plans to return")


class ExtractPreferencesInput(BaseModel):
    """Extract and store user preferences from interaction text."""

    source_text: str = Field(description="Text to analyze for preference signals")


class GetGoalMemoriesInput(BaseModel):
    """Get active user goals stored as memories.

    Goals are stored as memories with memory_type='goal' and scope='planning'.
    Returns goal text, confidence, and entity links.
    """

    limit: int = Field(default=10, ge=1, le=50, description="Maximum goals to return")


class BuildContextInput(BaseModel):
    """Build a rich context pack for a query/task."""

    query: str = Field(description="Query or task description to build context for")
    task_type: str = Field(default="", description="Optional task type for context relevance")


class VerifyRunInput(BaseModel):
    """Verify a completed run against success conditions."""

    run_id: str = Field(description="Run ID to verify")


class ReportGovernorVerdictInput(BaseModel):
    """Report the Governor's policy evaluation verdict."""

    verdict: Literal["auto_execute", "approval_required", "blocked"] = Field(
        description="Policy verdict"
    )
    risk_level: Literal["none", "low", "medium", "high", "critical"] = Field(
        description="Assessed risk level"
    )
    justification: str = Field(description="Reasoning for the verdict")
    conditions: list[str] = Field(default_factory=list, description="Conditions for approval")


class SendTelegramInput(BaseModel):
    """Send a message to the user via Telegram.

    Supports Markdown formatting and optional inline keyboard buttons.
    """

    text: str = Field(description="Message text (supports Markdown)")
    parse_mode: str = Field(default="Markdown", description="Format: Markdown or HTML")
    reply_markup: str = Field(
        default="", description="JSON string of inline keyboard markup (optional)"
    )


class SendApprovalPromptInput(BaseModel):
    """Send an approval request with interactive Approve/Reject buttons via Telegram."""

    approval_id: str = Field(description="ID of the pending approval")
    title: str = Field(description="Approval request title")
    summary: str = Field(description="Summary of what needs approval")
    risk_level: str = Field(default="medium", description="Risk level: low, medium, high, critical")


class PushUiUpdateInput(BaseModel):
    """Push a dynamic UI update to the web frontend via Redis pub/sub.

    Delivers A2UI surface payloads to connected browser sessions.
    """

    surface_id: str = Field(
        description="UI surface identifier (e.g., 'daily_brief', 'approval_detail')"
    )
    payload: str = Field(description="JSON string of the A2UI surface payload")


# ── Registry ───────────────────────────────────────────────────────

TOOL_INPUT_MODELS: dict[str, type[BaseModel]] = {
    "ingest_event": IngestEventInput,
    "search": SearchInput,
    "evaluate_policy": EvaluatePolicyInput,
    "get_briefing": GetBriefingInput,
    "get_observation_cursor": GetObservationCursorInput,
    "update_observation_cursor": UpdateObservationCursorInput,
    "report_observation": ReportObservationInput,
    "approve_action": ApproveActionInput,
    "update_execution": UpdateExecutionInput,
    "update_entity": UpdateEntityInput,
    "get_active_plans": GetActivePlansInput,
    "extract_preferences": ExtractPreferencesInput,
    "get_goal_memories": GetGoalMemoriesInput,
    "build_context": BuildContextInput,
    "verify_run": VerifyRunInput,
    "report_governor_verdict": ReportGovernorVerdictInput,
    "send_telegram": SendTelegramInput,
    "send_approval_prompt": SendApprovalPromptInput,
    "push_ui_update": PushUiUpdateInput,
}


def build_tool_definitions() -> list[dict]:
    """Generate Claude tool definitions from Pydantic models."""
    tools = []
    for tool_name, model_cls in TOOL_INPUT_MODELS.items():
        schema = model_cls.model_json_schema()
        # Claude expects input_schema with "type": "object" at top level
        # model_json_schema() produces this directly
        tools.append(
            {
                "name": tool_name,
                "description": model_cls.__doc__.strip() if model_cls.__doc__ else tool_name,
                "input_schema": schema,
            }
        )
    return tools
