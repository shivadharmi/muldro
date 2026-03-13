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


# ── Webhook ───────────────────────────────────────────────────────

class WebhookResponse(BaseModel):
    received: bool = True
    event_id: str | None = None


# ── Health ────────────────────────────────────────────────────────

class HealthResponse(BaseModel):
    status: str = "ok"
    version: str = "0.1.0"
