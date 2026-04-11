"""Pydantic schemas for API request/response contracts."""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

# ── Shared Type Literals ─────────────────────────────────────────

MemoryType = Literal[
    "episodic", "semantic", "preference", "relationship", "task_context", "procedural"
]
MemoryScope = Literal["presentation", "planning", "general"]
BriefingStyle = Literal["founder", "personal", "academic", "general"]

# ── Command ───────────────────────────────────────────────────────


class CommandRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    command: str
    context: str | None = None


class CommandResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    plan_id: str | None = None
    decision: str
    summary: str
    pending_approvals: list[dict] | None = None


# ── Briefing ──────────────────────────────────────────────────────


class BriefingResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
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
    model_config = ConfigDict(extra="ignore")
    feedback_type: str  # "rating" | "item_acted_on" | "item_dismissed" | "follow_up_asked"
    rating: int | None = None  # 1-5 when feedback_type="rating"
    item_section: str | None = None  # e.g. "top_priorities", "recommended_actions"
    item_index: int | None = None
    item_title: str | None = None
    comment: str | None = None
    extra_data: dict | None = None


class BriefingFeedbackResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    feedback_id: str
    briefing_id: str
    feedback_type: str
    status: str = "recorded"


class BriefingFeedbackSummary(BaseModel):
    model_config = ConfigDict(extra="ignore")
    briefing_id: str
    total_feedback: int = 0
    average_rating: float | None = None
    items_acted_on: int = 0
    items_dismissed: int = 0
    follow_ups_asked: int = 0


# ── Approval ──────────────────────────────────────────────────────


class ApprovalDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    reason: str | None = None


class ApprovalResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    approval_id: str
    status: str
    title: str
    summary: str | None = None
    risk_level: str = "medium"
    created_at: datetime | None = None


# ── Tasks ─────────────────────────────────────────────────────────


class TaskResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    task_id: str
    goal: str
    priority: str
    status: str
    decision: str
    created_at: datetime | None = None


# ── Search ────────────────────────────────────────────────────────


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    query: str
    scope: str | None = "all"  # memory, entities, events, all


class SearchResult(BaseModel):
    model_config = ConfigDict(extra="ignore")
    type: str  # memory, entity, event
    id: str
    title: str
    summary: str | None = None
    score: float | None = None


class SearchResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    results: list[SearchResult] = []


# ── Meeting Prep ──────────────────────────────────────────────────


class MeetingPrepRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    meeting_id: str | None = None
    next: bool | None = None


class MeetingPrepResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
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
    model_config = ConfigDict(extra="ignore")
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
    model_config = ConfigDict(extra="ignore")
    event_id: str | None = None
    status: str
    importance_score: float | None = None


# ── Canvas Dashboard ─────────────────────────────────────────────


class DashboardApproval(BaseModel):
    model_config = ConfigDict(extra="ignore")
    approval_id: str
    title: str
    summary: str | None = None
    risk_level: str = "medium"
    approval_type: str = ""
    created_at: datetime | None = None


class DashboardTask(BaseModel):
    model_config = ConfigDict(extra="ignore")
    task_id: str
    goal: str
    priority: str
    status: str
    decision: str
    step_count: int = 0
    steps_completed: int = 0
    created_at: datetime | None = None


class DashboardMeeting(BaseModel):
    model_config = ConfigDict(extra="ignore")
    event_id: str
    title: str
    starts_at: datetime | None = None
    attendee_count: int = 0
    location: str | None = None


class DashboardTrace(BaseModel):
    model_config = ConfigDict(extra="ignore")
    trace_id: str
    trigger: str
    agents_invoked: list[str] = []
    duration_ms: int | None = None
    total_cost_usd: float = 0.0


class DashboardGoal(BaseModel):
    model_config = ConfigDict(extra="ignore")
    goal_id: str
    title: str
    progress: float = 0.0
    priority: str = "medium"
    task_count: int = 0
    completed_task_count: int = 0


class DashboardEvent(BaseModel):
    model_config = ConfigDict(extra="ignore")
    source: str
    event_type: str
    title: str | None = None
    occurred_at: datetime | None = None


class DashboardResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    headline: str | None = None
    date: date
    pending_approvals: list[DashboardApproval] = []
    active_tasks: list[DashboardTask] = []
    upcoming_meetings: list[DashboardMeeting] = []
    recommended_actions: list[str] = []
    briefing_id: str | None = None
    recent_traces: list[DashboardTrace] = []
    active_goals: list[DashboardGoal] = []
    recent_events: list[DashboardEvent] = []


# ── Approval Detail ──────────────────────────────────────────────


class ApprovalDetailResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
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
    model_config = ConfigDict(extra="ignore")
    task_id: str
    task_type: str
    status: str
    result_summary: str | None = None


class TaskDetailResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
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
    model_config = ConfigDict(extra="ignore")
    source: str
    event_count: int = 0
    status: str = "ok"  # ok | error
    error_message: str | None = None


class PerceptionStatusResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
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
    model_config = ConfigDict(extra="ignore")
    name: str
    description: str | None = None
    schedule_type: str = "recurring"  # recurring | one_shot
    cron_expr: str | None = None
    run_at: datetime | None = None
    action_type: str
    action_config: dict | None = None
    enabled: bool = True
    source: str = "user"  # system | user | reflection
    priority: str = "medium"  # low | medium | high


class ScheduleUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str | None = None
    description: str | None = None
    cron_expr: str | None = None
    run_at: datetime | None = None
    action_type: str | None = None
    action_config: dict | None = None
    enabled: bool | None = None
    priority: str | None = None


class ScheduleResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
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
    model_config = ConfigDict(extra="ignore")
    status: str = "ok"
    version: str = "0.1.0"
