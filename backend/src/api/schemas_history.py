"""Pydantic response models for the History API."""

from datetime import datetime

from pydantic import BaseModel


class HistoryStepSummary(BaseModel):
    """Compact step info for the history list view (no output_data)."""

    step_id: str
    name: str | None = None
    capability: str | None = None
    status: str
    started_at: datetime | None = None
    completed_at: datetime | None = None


class HistoryApprovalContext(BaseModel):
    """Embedded approval context for runs awaiting approval."""

    approval_id: str
    step_id: str | None = None
    step_description: str | None = None
    risk_level: str = "low"
    trust_level: str | None = None


class HistoryItemResponse(BaseModel):
    """Single run in the history list."""

    run_id: str
    plan_id: str | None = None
    goal: str | None = None
    source: str | None = None
    trigger_type: str | None = None
    status: str
    risk_level: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error: dict | None = None
    retry_count: int = 0
    step_count: int = 0
    completed_step_count: int = 0
    cost_usd: float | None = None
    steps: list[HistoryStepSummary] = []
    approval: HistoryApprovalContext | None = None
    live_phase: str | None = None
    surface_id: str | None = None


class HistoryListResponse(BaseModel):
    """Paginated history list."""

    items: list[HistoryItemResponse]
    total: int
    limit: int
    offset: int


class HistoryArtifactRef(BaseModel):
    """Artifact reference in step detail."""

    artifact_id: str
    title: str | None = None
    artifact_type: str | None = None


class HistoryDetailStep(BaseModel):
    """Full step detail for the detail view (includes output_data)."""

    step_id: str
    name: str | None = None
    capability: str | None = None
    status: str
    input_data: dict | None = None
    output_data: dict | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_ms: int | None = None
    error: dict | None = None
    artifacts: list[HistoryArtifactRef] = []


class HistoryApprovalRecord(BaseModel):
    """Approval decision record in detail view."""

    approval_id: str
    step_id: str | None = None
    status: str
    risk_level: str = "low"
    title: str | None = None
    decided_at: datetime | None = None
    decision_reason: str | None = None
    approved_by: str | None = None


class HistoryPlanContext(BaseModel):
    """Plan context in detail view."""

    plan_id: str
    goal: str | None = None
    reasoning_summary: str | None = None
    success_conditions: list | None = None
    trigger_type: str | None = None
    priority: str | None = None


class HistoryTraceInfo(BaseModel):
    """Trace/cost info in detail view."""

    trace_id: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    duration_ms: int = 0
    agents_invoked: list[str] = []
    tools_called: list[str] = []


class HistoryEventEntry(BaseModel):
    """Runtime event in detail view."""

    event_type: str
    occurred_at: datetime
    step_id: str | None = None
    payload: dict = {}


class HistoryDetailResponse(BaseModel):
    """Full run detail for the detail modal."""

    run_id: str
    plan: HistoryPlanContext | None = None
    status: str
    source: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error: dict | None = None
    steps: list[HistoryDetailStep] = []
    approvals: list[HistoryApprovalRecord] = []
    trace: HistoryTraceInfo | None = None
    events: list[HistoryEventEntry] = []


class RunActionResponse(BaseModel):
    """Response for cancel/resume/retry actions."""

    run_id: str
    status: str
    message: str = ""
