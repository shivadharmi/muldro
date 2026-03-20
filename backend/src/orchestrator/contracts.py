"""Runtime contracts — Pydantic models at orchestrator boundaries.

Replaces raw dicts flowing between orchestrator, planner, agents,
and graph executor with validated, typed models.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class PlannerTask(BaseModel):
    """A single task within a planner output."""

    model_config = ConfigDict(extra="ignore")

    task_type: str
    input_data: dict[str, Any] = Field(default_factory=dict)


class PlannerOutput(BaseModel):
    """Validated planner decision — replaces raw JSON dict from Claude.

    Sources of truth for decision types:
    - Planner prompt (prompts.py): ignore, acknowledge, summarize, ask_user,
      recommend, create_task, draft_reply, schedule_reminder
    - Route resolver (route_resolver.py): research, observe, remember,
      watcher_create, goal_update
    - Orchestrator direct handling: answer_directly, search_memory, add_to_brief

    Add new decision types here FIRST, then to the planner prompt and routes.
    """

    model_config = ConfigDict(extra="ignore")

    decision: Literal[
        "acknowledge",
        "answer_directly",
        "create_task",
        "draft_reply",
        "search_memory",
        "add_to_brief",
        "ignore",
        "watcher_create",
        "goal_update",
        "research",
        "observe",
        "remember",
        "ask_user",
        "recommend",
        "summarize",
        "schedule_reminder",
    ] = "acknowledge"
    goal: str = ""
    reasoning: str = ""
    priority: Literal["low", "medium", "high", "critical"] = "medium"
    risk_level: Literal["none", "low", "medium", "high"] = "low"
    execution_mode: Literal["auto_execute", "approval_required", "draft_only"] = "approval_required"
    plan_id: str | None = None
    tasks: list[PlannerTask] = Field(default_factory=list)


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
    response_text: str = ""
    tools_called: list[str] = Field(default_factory=list)
    tokens_used: int = 0


class StepResult(BaseModel):
    """Result of a single execution step."""

    model_config = ConfigDict(extra="ignore")

    step_id: str
    status: str
    output_data: dict[str, Any] | None = None
    error: str | None = None
    duration_ms: int = 0


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
    decision: str | None = None
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
    decision: PlannerOutput | None = None
    agent_steps: list[MessageAgentStep] = Field(default_factory=list)


class DomainEvent(BaseModel):
    """Typed domain event flowing through the event bus."""

    model_config = ConfigDict(extra="ignore")

    event_type: str
    user_id: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)
    trace_id: str | None = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ExecutionPlan(BaseModel):
    """Structured plan DTO bridging Planner -> Governor."""

    model_config = ConfigDict(extra="ignore")

    plan_id: str
    goal: str
    tasks: list[PlannerTask] = Field(default_factory=list)
    risk_level: Literal["none", "low", "medium", "high"] = "low"
    execution_mode: Literal["auto_execute", "approval_required", "draft_only"] = "approval_required"
    priority: Literal["low", "medium", "high", "critical"] = "medium"
    reasoning_summary: str = ""


class PolicyDecision(BaseModel):
    """Governor verdict envelope."""

    model_config = ConfigDict(extra="ignore")

    decision: Literal["auto_execute", "approval_required", "blocked"]
    justification: str = ""
    risk_level: str = "low"
    approval_id: str | None = None
    execution_id: str | None = None
