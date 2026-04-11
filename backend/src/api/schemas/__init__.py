"""Pydantic schemas for API request/response contracts."""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

# ── Shared Type Literals ─────────────────────────────────────────

MemoryType = Literal[
    "episodic", "semantic", "preference", "relationship", "task_context", "procedural"
]
MemoryScope = Literal["presentation", "planning", "general"]
BriefingStyle = Literal["founder", "personal", "academic", "general"]

# ── Command ───────────────────────────────────────────────────────


class CommandRequest(BaseModel):
    command: str
    context: str | None = None


class CommandResponse(BaseModel):
    plan_id: str | None = None
    decision: str
    summary: str
    pending_approvals: list[dict] | None = None


# ── Briefing ──────────────────────────────────────────────────────


class BriefingResponse(BaseModel):
    briefing_id: str
    date: date
    headline: str | None = None
    top_priorities: list[dict] = []
    changes_since_last: list[dict] = []
    pending_approvals: list[dict] = []
    recommended_actions: list[str] = []
    full_text: str | None = None


# ── Briefing Feedback ─────────────────────────────────────────────


class BriefingFeedbackRequest(BaseModel):
    feedback_type: Literal["rating", "item_acted_on", "item_dismissed", "follow_up_asked"]
    rating: int | None = Field(None, ge=1, le=5)
    item_section: str | None = None
    item_index: int | None = None
    item_title: str | None = None
    comment: str | None = None
    extra_data: dict | None = None


class BriefingFeedbackResponse(BaseModel):
    feedback_id: str
    briefing_id: str
    feedback_type: str
    status: str = "recorded"


class BriefingFeedbackSummary(BaseModel):
    briefing_id: str
    total_feedback: int = 0
    average_rating: float | None = None
    items_acted_on: int = 0
    items_dismissed: int = 0
    follow_ups_asked: int = 0


# ── Approval ──────────────────────────────────────────────────────


class ApprovalDecisionRequest(BaseModel):
    reason: str | None = None


class ApprovalResponse(BaseModel):
    approval_id: str
    status: str
    title: str
    summary: str | None = None
    risk_level: str = "medium"
    created_at: datetime | None = None


# ── Tasks ─────────────────────────────────────────────────────────


class TaskResponse(BaseModel):
    task_id: str
    goal: str
    priority: str
    status: str
    decision: str
    created_at: datetime | None = None


# ── Search ────────────────────────────────────────────────────────


class SearchRequest(BaseModel):
    query: str
    scope: str | None = "all"


class SearchResult(BaseModel):
    type: str
    id: str
    title: str
    summary: str | None = None
    score: float | None = None
    source_db: str | None = None
    why_matched: str | None = None


class SearchResponse(BaseModel):
    results: list[SearchResult] = []


# ── Meeting Prep ──────────────────────────────────────────────────


class MeetingPrepRequest(BaseModel):
    meeting_id: str | None = None
    next: bool | None = None


class MeetingPrepResponse(BaseModel):
    meeting_id: str
    title: str
    starts_at: datetime | None = None
    attendees: list[dict] = []
    agenda: list[str] = []
    related_threads: list[dict] = []
    action_items: list[dict] = []
    risks: list[str] = []


# ── Event Ingestion ──────────────────────────────────────────────


class EventIngestRequest(BaseModel):
    source: str
    event_type: str
    entity_type: str
    entity_id: str
    title: str
    summary: str | None = None
    actor: dict | None = None
    occurred_at: datetime | None = None
    raw_payload: dict | None = None


class EventIngestResponse(BaseModel):
    event_id: str | None = None
    status: str
    importance_score: float | None = None


# ── Approval Detail ──────────────────────────────────────────────


class ApprovalDetailResponse(BaseModel):
    approval_id: str
    status: str
    title: str
    summary: str | None = None
    approval_type: str = ""
    risk_level: str = "medium"
    created_at: datetime | None = None
    decided_at: datetime | None = None
    decision_reason: str | None = None
    execution_id: str | None = None
    plan_goal: str | None = None
    artifact_refs: dict | None = None
    trace_id: str | None = None


# ── Task Detail ──────────────────────────────────────────────────


class TaskStepResponse(BaseModel):
    task_id: str
    task_type: str
    status: str
    result_summary: str | None = None


class TaskDetailResponse(BaseModel):
    task_id: str
    goal: str
    priority: str
    status: str
    decision: str
    risk_level: str = "low"
    reasoning_summary: str | None = None
    steps: list[TaskStepResponse] = []
    execution_status: str | None = None
    created_at: datetime | None = None


# ── Observation ──────────────────────────────────────────────────


class PerceptionReportRequest(BaseModel):
    source: str
    event_count: int = 0
    status: str = "ok"  # ok | error
    error_message: str | None = None


class PerceptionStatusResponse(BaseModel):
    source: str
    last_run_at: datetime | None = None
    event_count: int = 0
    circuit_state: str = "closed"  # closed | open | half_open
    error_message: str | None = None
    consecutive_failures: int = 0
    total_runs: int = 0
    is_stale: bool = False


# ── Schedule ─────────────────────────────────────────────────────


class ScheduleCreateRequest(BaseModel):
    name: str
    description: str | None = None
    schedule_type: Literal["recurring", "one_shot"] = "recurring"
    cron_expr: str | None = None
    run_at: datetime | None = None
    action_type: str
    action_config: dict | None = None
    enabled: bool = True
    source: Literal["system", "user", "reflection"] = "user"
    priority: Literal["low", "medium", "high"] = "medium"


class ScheduleUpdateRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    cron_expr: str | None = None
    run_at: datetime | None = None
    action_type: str | None = None
    action_config: dict | None = None
    enabled: bool | None = None
    priority: Literal["low", "medium", "high"] | None = None


class ScheduleResponse(BaseModel):
    schedule_id: str
    user_id: str
    name: str
    description: str | None = None
    schedule_type: str
    cron_expr: str | None = None
    run_at: datetime | None = None
    action_type: str
    action_config: dict | None = None
    enabled: bool
    last_run_at: datetime | None = None
    next_run_at: datetime | None = None
    run_count: int = 0
    consecutive_failures: int = 0
    last_error: str | None = None
    source: str
    priority: str
    created_at: datetime | None = None
    updated_at: datetime | None = None


# ── Health ────────────────────────────────────────────────────────


class HealthResponse(BaseModel):
    status: str = "ok"
    version: str = "0.1.0"
