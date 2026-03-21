"""Pydantic response models for GET /v1/home."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class CapabilityHealthItem(BaseModel):
    family: str
    status: str  # healthy, degraded, unavailable, unconfigured
    provider: str | None = None
    last_activity_at: datetime | None = None
    capabilities_available: int = 0
    capabilities_total: int = 0
    message: str | None = None


class PriorityItem(BaseModel):
    item_type: str  # approval, alert, briefing, workflow_blocked
    item_id: str
    title: str
    priority: str  # critical, high, medium
    created_at: datetime | None = None
    action_url: str | None = None


class LiveActivityItem(BaseModel):
    event_type: str
    description: str
    occurred_at: datetime
    run_id: str | None = None
    agent_name: str | None = None


class RecommendedAction(BaseModel):
    action_type: str
    title: str
    description: str
    priority: str = "medium"
    action_url: str | None = None


class RecentIntelligenceItem(BaseModel):
    item_type: str  # briefing, memory, entity_update, observation
    item_id: str
    title: str
    summary: str
    created_at: datetime | None = None


class HomeResponse(BaseModel):
    since_last_visit: datetime | None = None
    priority_items: list[PriorityItem] = []
    live_activity: list[LiveActivityItem] = []
    recommended_actions: list[RecommendedAction] = []
    recent_intelligence: list[RecentIntelligenceItem] = []
    capability_health: list[CapabilityHealthItem] = []
