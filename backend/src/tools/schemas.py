"""Auto-generated tool schemas from Pydantic models.

Each tool input is a Pydantic BaseModel with Field descriptions.
Schemas are generated via .model_json_schema() — single source of truth.
"""

from typing import Literal

from croniter import croniter
from pydantic import BaseModel, ConfigDict, Field, field_validator

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


class PushUiUpdateInput(BaseModel):
    """Push a dynamic UI update to the web frontend via Redis pub/sub.

    Delivers A2UI surface payloads to connected browser sessions.
    """

    surface_id: str = Field(
        description="UI surface identifier (e.g., 'daily_brief', 'approval_detail')"
    )
    payload: str = Field(description="JSON string of the A2UI surface payload")


class StoreMemoryInput(BaseModel):
    """Store a memory in the knowledge base.

    Memories are typed (fact, goal, preference, briefing_item, task_context)
    and scoped (general, planning, personal). TTL controls retention.
    """

    text: str = Field(description="Memory content text")
    memory_type: str = Field(
        default="fact",
        description="Memory type: fact, goal, preference, briefing_item, task_context",
    )
    scope: str = Field(
        default="general",
        description="Memory scope: general, planning, personal",
    )
    ttl_days: int = Field(
        default=0,
        ge=0,
        description="Time-to-live in days. 0 = no expiry.",
    )
    entity_ids: str = Field(
        default="",
        description="Comma-separated entity IDs to link to this memory",
    )
    source: str = Field(
        default="agent",
        description="Origin of this memory: agent, perception, user",
    )


class StorePreferenceInput(BaseModel):
    """Store a user preference extracted from interactions.

    Preferences are memories with memory_type='preference' and long TTL.
    Used by Persona agent after extracting preference signals.
    """

    text: str = Field(description="Preference description (e.g., 'Prefers morning meetings')")
    confidence: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="Confidence in this preference (0.0-1.0)",
    )
    source_text: str = Field(
        default="",
        description="Original text the preference was extracted from",
    )


class GetPlanDetailsInput(BaseModel):
    """Fetch plan metadata to verify existence and inspect tasks.

    Returns plan goal, priority, risk level, decision type, status,
    creation time, and task list. Used by Governor to independently
    verify that a plan_id corresponds to a legitimate plan.
    """

    plan_id: str = Field(description="ID of the plan to look up")


class DiscoverCapabilitiesInput(BaseModel):
    """Search available capabilities by query.

    Returns matching capabilities with descriptions, tools, risk levels,
    and connection status.
    """

    query: str = Field(description="Search query, e.g. 'email', 'calendar management'")


class GetEntityInput(BaseModel):
    """Fetch a world-model entity + its current attribute beliefs."""

    entity_id: str = Field(description="Entity id (ent_...) to fetch")


class QueryFactsInput(BaseModel):
    """Query an entity's attribute beliefs as-of a timestamp (bi-temporal)."""

    entity_id: str = Field(description="Entity id (ent_...) to query")
    as_of: str = Field(default="", description="ISO-8601 timestamp; empty = now")


class TraverseInput(BaseModel):
    """List the relationships incident to an entity (one hop)."""

    entity_id: str = Field(description="Entity id (ent_...) to traverse from")


class GetProvenanceInput(BaseModel):
    """Provenance for an entity's current beliefs."""

    entity_id: str = Field(description="Entity id (ent_...)")
    attr_key: str = Field(default="", description="Optional single attribute key")


class SetGoalInput(BaseModel):
    """Record a user goal so Jarvis can track and act toward it. Use when the user states an
    objective ("my goal is …", "I want to …", "remember I'm trying to …")."""

    title: str = Field(description="The goal statement, e.g. 'Close the seed round by Q3'")
    priority: str = Field(default="medium", description="Goal priority: low, medium, or high")


class SetInstructionInput(BaseModel):
    """Record a standing user instruction or preference so future turns honor it. Use when the
    user says "always …", "from now on …", "remember to …", "I prefer …". For time-based
    reminders use ``schedule_reminder`` instead — this tool stores a durable preference, it does
    not create schedules or triggers."""

    instruction_text: str = Field(description="The instruction or preference, verbatim")
    instruction_type: str = Field(
        default="preference",
        description="Instruction category label (stored on the preference memory), e.g. "
        "'preference'",
    )


class SetInstructionStepInput(BaseModel):
    """Planner ``system.set_instruction`` STEP input (capability path only, NOT
    the direct MCP tool schema). Supports the richer trigger/schedule creation
    that ``_handle_set_instruction`` performs."""

    model_config = ConfigDict(extra="ignore")

    instruction_text: str = Field(description="The instruction or preference, verbatim")
    instruction_type: str = Field(default="preference", description="Instruction category label")
    trigger_conditions: dict | None = Field(
        default=None, description="Optional trigger match conditions"
    )
    schedule_config: dict | None = Field(default=None, description="Optional schedule config")


class ScheduleReminderInput(BaseModel):
    """Create a one-shot reminder. Use when the user asks to be reminded of something ("remind
    me to …", "ping me about …")."""

    title: str = Field(description="What to remind the user about")
    cron_expr: str = Field(
        default="", description="Optional cron/timing expression for the reminder"
    )

    @field_validator("cron_expr")
    @classmethod
    def _validate_cron(cls, v: str) -> str:
        """Reject a malformed cron at model-validation time.

        Empty means "no recurrence". A non-empty value MUST be a well-formed
        croniter expression — an LLM-supplied garbage cron would otherwise be
        persisted and later crash the scheduler's dispatch sweep
        (CroniterBadCronError). Enforcing it here means every ``model_validate``
        call (tool path and capability path) rejects it structurally.
        """
        if v and not croniter.is_valid(v):
            raise ValueError(f"invalid cron expression: {v!r}")
        return v


class AddToBriefInput(BaseModel):
    """Add an item to the user's next daily briefing. Use when the user says "add this to my
    briefing / brief", "surface this tomorrow", "flag this for my next update"."""

    text: str = Field(description="The briefing item text")


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
    "push_ui_update": PushUiUpdateInput,
    "store_memory": StoreMemoryInput,
    "store_preference": StorePreferenceInput,
    "get_plan_details": GetPlanDetailsInput,
    "discover_capabilities": DiscoverCapabilitiesInput,
    "get_entity": GetEntityInput,
    "query_facts": QueryFactsInput,
    "traverse": TraverseInput,
    "get_provenance": GetProvenanceInput,
    "set_goal": SetGoalInput,
    "set_instruction": SetInstructionInput,
    "schedule_reminder": ScheduleReminderInput,
    "add_to_brief": AddToBriefInput,
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
