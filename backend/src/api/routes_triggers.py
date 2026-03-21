"""Trigger CRUD routes."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID

from src.api.deps import get_current_user_id, get_current_workspace_id, get_session
from src.models.triggers import Trigger

router = APIRouter()
logger = logging.getLogger(__name__)

# ── Schema definitions for frontend ──────────────────────────────

EVENT_TYPES: list[dict] = [
    {"value": "email.received", "label": "Email Received", "source": "gmail"},
    {"value": "email.sent", "label": "Email Sent", "source": "gmail"},
    {"value": "email.important", "label": "Important Email", "source": "gmail"},
    {"value": "calendar.event_created", "label": "Calendar Event Created", "source": "calendar"},
    {"value": "calendar.event_updated", "label": "Calendar Event Updated", "source": "calendar"},
    {"value": "calendar.event_starting", "label": "Meeting Starting Soon", "source": "calendar"},
    {"value": "calendar.event_cancelled", "label": "Event Cancelled", "source": "calendar"},
    {"value": "slack.message", "label": "Slack Message", "source": "slack"},
    {"value": "slack.mention", "label": "Slack Mention", "source": "slack"},
    {"value": "slack.dm", "label": "Slack Direct Message", "source": "slack"},
    {"value": "github.pr_opened", "label": "PR Opened", "source": "github"},
    {"value": "github.pr_reviewed", "label": "PR Reviewed", "source": "github"},
    {"value": "github.pr_merged", "label": "PR Merged", "source": "github"},
    {"value": "github.issue_opened", "label": "Issue Opened", "source": "github"},
    {"value": "github.issue_assigned", "label": "Issue Assigned", "source": "github"},
    {"value": "github.push", "label": "Code Push", "source": "github"},
    {"value": "github.ci_failed", "label": "CI Failed", "source": "github"},
    {"value": "linear.issue_created", "label": "Linear Issue Created", "source": "linear"},
    {"value": "linear.issue_updated", "label": "Linear Issue Updated", "source": "linear"},
    {"value": "linear.issue_assigned", "label": "Linear Issue Assigned", "source": "linear"},
    {"value": "notion.page_updated", "label": "Notion Page Updated", "source": "notion"},
    {"value": "jira.issue_created", "label": "Jira Issue Created", "source": "jira"},
    {"value": "jira.issue_updated", "label": "Jira Issue Updated", "source": "jira"},
    {"value": "system.anomaly", "label": "System Anomaly Detected", "source": "system"},
    {"value": "system.budget_warning", "label": "Budget Warning", "source": "system"},
    {"value": "plan.completed", "label": "Plan Completed", "source": "system"},
    {"value": "plan.failed", "label": "Plan Failed", "source": "system"},
    {"value": "approval.requested", "label": "Approval Requested", "source": "system"},
]

TRIGGER_ACTION_TYPES: list[dict] = [
    {
        "value": "notify",
        "label": "Notify",
        "description": "Send a notification to the user",
        "config_fields": [
            {
                "name": "channel",
                "type": "select",
                "label": "Channel",
                "required": False,
                "default": "web",
                "options": [
                    {"value": "web", "label": "Web"},
                    {"value": "telegram", "label": "Telegram"},
                    {"value": "email", "label": "Email"},
                ],
            },
            {
                "name": "message_template",
                "type": "text",
                "label": "Message Template",
                "required": False,
                "placeholder": "Custom notification message (optional)",
            },
        ],
    },
    {
        "value": "plan",
        "label": "Create Plan",
        "description": "Create an execution plan in response to the event",
        "config_fields": [
            {
                "name": "goal",
                "type": "text",
                "label": "Goal",
                "required": False,
                "placeholder": "What should the plan accomplish?",
            },
        ],
    },
    {
        "value": "escalate",
        "label": "Escalate",
        "description": "Escalate to high priority and notify immediately",
        "config_fields": [
            {
                "name": "urgency",
                "type": "select",
                "label": "Urgency",
                "required": False,
                "default": "high",
                "options": [
                    {"value": "medium", "label": "Medium"},
                    {"value": "high", "label": "High"},
                    {"value": "critical", "label": "Critical"},
                ],
            },
        ],
    },
    {
        "value": "procedure",
        "label": "Run Procedure",
        "description": "Execute a predefined procedure or workflow",
        "config_fields": [
            {
                "name": "procedure_name",
                "type": "text",
                "label": "Procedure Name",
                "required": True,
                "placeholder": "e.g., deploy_review, triage_issue",
            },
            {
                "name": "params",
                "type": "json",
                "label": "Parameters",
                "required": False,
                "placeholder": "{}",
            },
        ],
    },
]

CONDITION_FIELDS: list[dict] = [
    {
        "name": "event_type",
        "type": "select",
        "label": "Event Type",
        "required": True,
        "description": "The event that triggers this rule",
    },
    {
        "name": "source",
        "type": "select",
        "label": "Source",
        "required": False,
        "description": "Filter by source (auto-set from event type)",
        "options": [
            {"value": "gmail", "label": "Gmail"},
            {"value": "calendar", "label": "Google Calendar"},
            {"value": "slack", "label": "Slack"},
            {"value": "github", "label": "GitHub"},
            {"value": "linear", "label": "Linear"},
            {"value": "notion", "label": "Notion"},
            {"value": "jira", "label": "Jira"},
            {"value": "system", "label": "System"},
        ],
    },
    {
        "name": "importance_threshold",
        "type": "select",
        "label": "Minimum Importance",
        "required": False,
        "description": "Only trigger for events at or above this importance level",
        "options": [
            {"value": "low", "label": "Low"},
            {"value": "medium", "label": "Medium"},
            {"value": "high", "label": "High"},
            {"value": "critical", "label": "Critical"},
        ],
    },
    {
        "name": "entity_match",
        "type": "text",
        "label": "Entity Match",
        "required": False,
        "description": "Only trigger when a specific entity is involved (e.g., person name, repo)",
        "placeholder": "e.g., john@example.com, myorg/myrepo",
    },
    {
        "name": "time_window",
        "type": "text",
        "label": "Time Window",
        "required": False,
        "description": "Only trigger during a time window (cron-like)",
        "placeholder": "e.g., 9-17 (9am to 5pm)",
    },
]


class TriggerItem(BaseModel):
    trigger_id: str
    name: str
    description: str | None = None
    conditions: dict
    action_type: str
    action_config: dict | None = None
    enabled: bool = True
    fire_count: int = 0
    last_fired_at: str | None = None
    created_at: str | None = None

    model_config = {"from_attributes": True}


class TriggerListResponse(BaseModel):
    triggers: list[TriggerItem]


class TriggerCreateRequest(BaseModel):
    name: str
    description: str | None = None
    conditions: dict
    action_type: str
    action_config: dict | None = None
    enabled: bool = True


class TriggerPatchRequest(BaseModel):
    enabled: bool | None = None
    name: str | None = None
    conditions: dict | None = None
    action_type: str | None = None
    action_config: dict | None = None


def _to_item(t: Trigger) -> TriggerItem:
    return TriggerItem(
        trigger_id=t.trigger_id,
        name=t.name,
        description=t.description,
        conditions=t.conditions,
        action_type=t.action_type,
        action_config=t.action_config,
        enabled=t.enabled,
        fire_count=t.fire_count,
        last_fired_at=t.last_fired_at.isoformat() if t.last_fired_at else None,
        created_at=t.created_at.isoformat() if t.created_at else None,
    )


@router.get("/v1/triggers/schema")
async def get_trigger_schema():
    """Return schema info for creating triggers.

    Includes event types (grouped by source), action types with config fields,
    and condition field definitions.
    """
    # Group event types by source for easier frontend rendering
    sources: dict[str, list[dict]] = {}
    for evt in EVENT_TYPES:
        src = evt["source"]
        if src not in sources:
            sources[src] = []
        sources[src].append({"value": evt["value"], "label": evt["label"]})

    return {
        "event_types": EVENT_TYPES,
        "event_types_by_source": sources,
        "action_types": TRIGGER_ACTION_TYPES,
        "condition_fields": CONDITION_FIELDS,
    }


@router.get("/v1/triggers", response_model=TriggerListResponse)
async def list_triggers(
    limit: int = Query(50, ge=1, le=200),
    user_id: str = Depends(get_current_user_id),
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_session),
):
    """List triggers for the current user."""
    stmt = (
        select(Trigger)
        .where(Trigger.user_id == user_id, Trigger.workspace_id == workspace_id)
        .order_by(Trigger.created_at.desc())
        .limit(limit)
    )
    result = await db.execute(stmt)
    rows = result.scalars().all()
    return TriggerListResponse(triggers=[_to_item(t) for t in rows])


@router.post("/v1/triggers", response_model=TriggerItem, status_code=201)
async def create_trigger(
    req: TriggerCreateRequest,
    user_id: str = Depends(get_current_user_id),
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_session),
):
    """Create a new trigger."""
    trigger = Trigger(
        trigger_id=f"trg_{ULID()}",
        user_id=user_id,
        workspace_id=workspace_id,
        name=req.name,
        description=req.description,
        conditions=req.conditions,
        action_type=req.action_type,
        action_config=req.action_config,
        enabled=req.enabled,
    )
    db.add(trigger)
    await db.commit()
    await db.refresh(trigger)
    return _to_item(trigger)


@router.patch("/v1/triggers/{trigger_id}", response_model=TriggerItem)
async def patch_trigger(
    trigger_id: str,
    req: TriggerPatchRequest,
    user_id: str = Depends(get_current_user_id),
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_session),
):
    """Update a trigger (partial)."""
    result = await db.execute(
        select(Trigger).where(
            Trigger.trigger_id == trigger_id,
            Trigger.user_id == user_id,
            Trigger.workspace_id == workspace_id,
        )
    )
    trigger = result.scalar_one_or_none()
    if not trigger:
        raise HTTPException(status_code=404, detail="Trigger not found")

    for field, value in req.model_dump(exclude_none=True).items():
        setattr(trigger, field, value)

    await db.commit()
    await db.refresh(trigger)
    return _to_item(trigger)


@router.delete("/v1/triggers/{trigger_id}", status_code=204)
async def delete_trigger(
    trigger_id: str,
    user_id: str = Depends(get_current_user_id),
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_session),
):
    """Delete a trigger."""
    result = await db.execute(
        select(Trigger).where(
            Trigger.trigger_id == trigger_id,
            Trigger.user_id == user_id,
            Trigger.workspace_id == workspace_id,
        )
    )
    trigger = result.scalar_one_or_none()
    if not trigger:
        raise HTTPException(status_code=404, detail="Trigger not found")

    await db.delete(trigger)
    await db.commit()
