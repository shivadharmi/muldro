"""Pydantic response models for runtime APIs."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class RuntimeStepResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    step_id: str
    status: str
    action: str | None = None
    tool_name: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error: str | None = None


class RuntimeRunResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    run_id: str
    plan_id: str | None = None
    status: str
    created_at: datetime | None = None
    total_steps: int = 0
    completed_steps: int = 0
    progress_pct: int = 0
    blocking_step_id: str | None = None
    blocking_reason: str | None = None


class RuntimeEventResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    event_id: str
    run_id: str | None = None
    step_id: str | None = None
    event_type: str
    occurred_at: datetime
    payload: dict = Field(default_factory=dict)


class AgentWorkloadResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    agent_name: str
    call_count_24h: int
    avg_duration_ms: float


class RuntimeSummaryResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    active_runs: int = 0
    blocked_runs: int = 0
    completed_24h: int = 0
    failed_24h: int = 0
    agents_active: int = 0
    active_agents: list[str] = Field(default_factory=list)
    top_agents: list[AgentWorkloadResponse] = []
