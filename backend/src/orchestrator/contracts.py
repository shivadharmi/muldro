"""Runtime contracts — Pydantic models at orchestrator boundaries.

Replaces raw dicts flowing between orchestrator, planner, agents,
and graph executor with validated, typed models.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class AgentEnvelope(BaseModel):
    """Input envelope for a sub-agent call."""

    model_config = ConfigDict(extra="ignore")

    agent_name: str
    message: str
    context: str = ""
    tools_available: list[str] = Field(default_factory=list)


class AgentResult(BaseModel):
    """Result envelope from a sub-agent call."""

    model_config = ConfigDict(extra="ignore")

    agent_name: str
    response_text: str | None = None
    tools_called: list[str] = Field(default_factory=list)
    tokens_used: int = 0


class StepResult(BaseModel):
    """Result of a single execution step."""

    model_config = ConfigDict(extra="ignore")

    step_id: str
    status: str
    output_data: dict[str, Any] | None = None
    error: str | None = None
    duration_ms: int | None = None


class ToolCallRequest(BaseModel):
    """A tool call request from an agent."""

    model_config = ConfigDict(extra="ignore")

    tool_name: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    requires_approval: bool = False


class ToolCallResult(BaseModel):
    """Result of a tool call execution."""

    model_config = ConfigDict(extra="ignore")

    tool_name: str
    status: Literal["success", "error", "blocked"] = "success"
    result: dict[str, Any] | None = None
    error: str | None = None
    duration_ms: int = 0


class SpanToolCall(BaseModel):
    """A tool call within an agent span — captures full input/output for replay."""

    model_config = ConfigDict(extra="ignore")

    tool_name: str
    input_data: dict[str, Any] = Field(default_factory=dict)
    output_data: Any | None = None
    status: Literal["success", "error", "blocked"] = "success"
    error: str | None = None
    duration_ms: int = 0


class SpanRecord(BaseModel):
    """Pydantic representation of a completed agent span for persistence."""

    model_config = ConfigDict(extra="ignore")

    span_id: str
    agent_name: str
    parent_span_id: str | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    duration_ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    thinking_tokens: int = 0
    model: str = "unknown"
    cost_usd: float = 0.0
    tools_called: list[str] = Field(default_factory=list)
    tool_call_details: list[SpanToolCall] = Field(default_factory=list)
    thinking_summary: str | None = None
    response_text: str | None = None
    error: str | None = None


class MessageToolCall(BaseModel):
    """A tool call persisted within a message's agent step."""

    model_config = ConfigDict(extra="ignore")

    tool_name: str
    tool_input: dict[str, Any] = Field(default_factory=dict)
    result_preview: str | None = None
    status: Literal["success", "error", "blocked"] = "success"
    duration_ms: int = 0


class MessageAgentStep(BaseModel):
    """An agent invocation persisted within a message's metadata."""

    model_config = ConfigDict(extra="ignore")

    agent: str
    model: str | None = None
    status: Literal["done", "error"] = "done"
    response_text: str | None = None
    thinking_preview: str | None = None
    reasoning_text: str | None = None
    tool_calls: list[MessageToolCall] = Field(default_factory=list)
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_creation_tokens: int | None = None
    cache_read_tokens: int | None = None
    cost_usd: float | None = None
    latency_ms: int | None = None


class MessageMetadata(BaseModel):
    """Typed metadata stored in the message JSONB column."""

    model_config = ConfigDict(extra="ignore")

    trace_id: str | None = None
    decision: PlanOutput | None = None
    agent_steps: list[MessageAgentStep] = Field(default_factory=list)


class DomainEvent(BaseModel):
    """Typed domain event flowing through the event bus."""

    model_config = ConfigDict(extra="ignore")

    event_type: str
    user_id: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)
    trace_id: str | None = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class PerceptionDecision(BaseModel):
    """Agent-informed perception policy returned after a perception cycle.

    The planner optionally includes this in its response to control how soon
    a source should next be checked, what entities to watch, and the urgency
    level.  The runtime clamps all values within system guardrails.
    """

    model_config = ConfigDict(extra="ignore")

    next_check_seconds: int | None = Field(None, ge=30)
    mode: Literal["poll", "push", "hybrid", "paused"] | None = None
    watch_entities: list[str] = Field(default_factory=list)
    urgency: Literal["low", "normal", "high"] = "normal"
    reasoning: str = ""
    notification_tier: Literal["push", "briefing", "silent"] | None = None


class PolicyDecision(BaseModel):
    """Governor verdict envelope."""

    model_config = ConfigDict(extra="ignore")

    # Produced by: Governor (auto_execute, blocked), TrustEngine (auto_execute_notify,
    # auto_execute_silent, approval_required). Union of all producers.
    decision: Literal[
        "auto_execute",
        "auto_execute_notify",
        "auto_execute_silent",
        "approval_required",
        "blocked",
    ]
    justification: str = ""
    risk_level: Literal["none", "low", "medium", "high", "critical"] = "low"
    approval_id: str | None = None
    execution_id: str | None = None
    trust_level: str = ""
    effective_trust_level: str = ""
    approved_count: int = 0
    rejected_count: int = 0


# ── Realtime / A2UI contracts ────────────────────────────────────


class RealtimeEventPayload(BaseModel):
    """Payload published to Redis Pub/Sub for real-time SSE subscribers."""

    model_config = ConfigDict(extra="ignore")

    event_id: str
    event_type: str
    user_id: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str = ""


class WorkspaceSurfacePush(BaseModel):
    """Full surface push payload sent via WebSocket / Redis Pub/Sub.

    Two-layer model: ``preview`` drives the workspace grid card,
    ``detail_config`` tells the frontend which tabs to show in the
    detail modal and where to fetch each tab's content.

    The old ``children`` + ``WorkspaceSurfaceMetadata`` shape is removed —
    grid cards render from SurfacePreview data, not A2UI component trees.
    """

    model_config = ConfigDict(extra="ignore")

    type: Literal["surface"] = "surface"
    id: str
    kind: Literal[
        "summary",
        "briefing",
        "plan",
        "checklist",
        "approval",
        "comparison",
        "alert",
        "timeline",
        "table",
        "recommendation",
        "activity",
        "proactive_insight",
    ]
    preview: Any  # SurfacePreview — imported at runtime to avoid circular deps
    detail_config: Any | None = None  # DetailConfig — same reason
    decision: str | None = None
    source_run_id: str | None = None
    response_preview: str | None = None
    created_at: str = ""
    ttl_hours: int = 24


class SuggestedActionRef(BaseModel):
    """Reference to a suggested action stored in the surface payload."""

    model_config = ConfigDict(extra="ignore")

    description: str
    capability: str
    action_input: dict[str, Any] = Field(default_factory=dict)


class InsightSurfaceData(BaseModel):
    """Data payload for proactive_insight surfaces, stored in UISurface.payload."""

    model_config = ConfigDict(extra="ignore")

    signal_source: str
    signal_category: str = ""
    signal_summary: str
    relevance_score: float = 0.0
    relevance_reasoning: str = ""
    related_goals: list[str] = Field(default_factory=list)
    suggested_actions: list[SuggestedActionRef] = Field(default_factory=list)
    dismiss_available: bool = True


# ── Execution surface update contracts ────────────────────────────


class StepState(BaseModel):
    """Live status of a single execution step."""

    model_config = ConfigDict(extra="ignore")

    step_id: str
    description: str
    status: Literal["pending", "executing", "completed", "failed", "approval_needed", "user_action"]
    output_summary: str | None = None
    duration_ms: int | None = None
    started_at: str | None = None

    # Evidence (available on demand)
    completed_at: str | None = None
    timeout_seconds: int | None = None
    error: dict | None = None
    retry_count: int | None = None


class ApprovalContext(BaseModel):
    """Context for an approval gate within a surface update."""

    model_config = ConfigDict(extra="ignore")

    # Primary
    approval_id: str
    step_description: str
    risk_level: str = ""
    trust_level: str = ""
    expires_at: str | None = None
    triggering_step_id: str | None = None
    graduation_hint: str = ""

    # Evidence
    risk_reasoning: str
    trust_context: str
    reversible: bool = True
    blast_radius: str = "self"
    effective_trust_level: str = ""
    approved_count: int = 0
    rejected_count: int = 0


class ResultSummary(BaseModel):
    """Summary of completed execution results."""

    model_config = ConfigDict(extra="ignore")

    key_findings: list[str] = Field(default_factory=list)
    artifacts_created: list[str] = Field(default_factory=list)
    suggested_next: list[str] = Field(default_factory=list)


class SurfaceUpdate(BaseModel):
    """Live execution progress pushed to workspace surfaces.

    Published to Redis channel jarvis:a2ui:{user_id} with
    type='surface_update'. The frontend applies incremental
    updates to the matching surface_id.
    """

    model_config = ConfigDict(extra="ignore")

    surface_id: str
    phase: Literal[
        "planning", "plan_ready", "executing", "approval_needed", "completed", "failed", "partial"
    ]
    steps: list[StepState] = Field(default_factory=list)
    current_step: str | None = None
    progress: str = ""
    approval: ApprovalContext | None = None
    results: ResultSummary | None = None


# ── Capability-based planning contracts ─────────────────────────────


class CapabilityGap(BaseModel):
    """A capability the plan needs but doesn't have."""

    model_config = ConfigDict(extra="ignore")

    description: str
    resolution: str  # e.g. "connect Notion" or "not currently possible"
    workaround: str | None = None


class PlanStep(BaseModel):
    """A single step in a capability-based plan."""

    model_config = ConfigDict(extra="ignore")

    step_id: str = ""
    description: str
    actor: Literal["jarvis", "user"] = "jarvis"
    capability: str  # e.g. "email.search", "reason", "respond"
    input: dict[str, Any] = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list)
    risk: Literal["none", "low", "medium", "high"] = "none"
    user_context: str | None = None


class PlanOutput(BaseModel):
    """Validated planner output — a goal-decomposed plan."""

    model_config = ConfigDict(extra="ignore")

    goal: str
    reasoning: str = ""
    achievable: Literal["full", "partial", "not_achievable"] = "full"
    priority: Literal["low", "medium", "high", "critical"] = "medium"
    steps: list[PlanStep] = Field(default_factory=list)
    success_criteria: str = ""
    capability_gaps: list[CapabilityGap] = Field(default_factory=list)
    plan_id: str | None = None
    requires_user_input: bool = False

    @model_validator(mode="after")
    def _validate_step_dependencies(self) -> PlanOutput:
        # Check step_id uniqueness
        seen_ids: set[str] = set()
        for step in self.steps:
            if step.step_id:
                if step.step_id in seen_ids:
                    raise ValueError(f"Duplicate step_id: '{step.step_id}'")
                seen_ids.add(step.step_id)

        step_ids = {s.step_id for s in self.steps if s.step_id}
        for step in self.steps:
            if step.step_id and step.step_id in step.depends_on:
                raise ValueError(f"Step '{step.step_id}' depends on itself")
            for dep in step.depends_on:
                if dep and dep not in step_ids:
                    raise ValueError(f"Step '{step.step_id}' depends on unknown step '{dep}'")
        # Cycle detection via DFS
        visited: set[str] = set()
        temp: set[str] = set()
        adj = {s.step_id: s.depends_on for s in self.steps if s.step_id}

        def visit(node: str) -> None:
            if node in temp:
                raise ValueError(f"Circular dependency detected involving '{node}'")
            if node in visited:
                return
            temp.add(node)
            for dep in adj.get(node, []):
                if dep:
                    visit(dep)
            temp.remove(node)
            visited.add(node)

        for sid in adj:
            visit(sid)
        return self
