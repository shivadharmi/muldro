"""Pydantic schemas for API request/response contracts."""

from datetime import date, datetime

from pydantic import BaseModel

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
    scope: str | None = "all"  # memory, entities, events, all


class SearchResult(BaseModel):
    type: str  # memory, entity, event
    id: str
    title: str
    summary: str | None = None
    score: float | None = None


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


# ── Webhook (legacy alias) ──────────────────────────────────────

WebhookResponse = EventIngestResponse


# ── Canvas Dashboard ─────────────────────────────────────────────


class DashboardApproval(BaseModel):
    approval_id: str
    title: str
    summary: str | None = None
    risk_level: str = "medium"
    approval_type: str = ""
    created_at: datetime | None = None


class DashboardTask(BaseModel):
    task_id: str
    goal: str
    priority: str
    status: str
    decision: str
    step_count: int = 0
    steps_completed: int = 0
    created_at: datetime | None = None


class DashboardMeeting(BaseModel):
    event_id: str
    title: str
    starts_at: datetime | None = None
    attendee_count: int = 0
    location: str | None = None


class DashboardResponse(BaseModel):
    headline: str | None = None
    date: date
    pending_approvals: list[DashboardApproval] = []
    active_tasks: list[DashboardTask] = []
    upcoming_meetings: list[DashboardMeeting] = []
    recommended_actions: list[str] = []
    briefing_id: str | None = None


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


# ── Health ────────────────────────────────────────────────────────


class HealthResponse(BaseModel):
    status: str = "ok"
    version: str = "0.1.0"
