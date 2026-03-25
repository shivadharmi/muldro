"""Meeting Prep workflow — look up next meeting, gather context, generate prep.

Uses Google Workspace MCP server (preferred) or Calendar integration fallback
for meeting data, world model for attendee entities, memory service for
related context, and Claude for synthesis.
"""

import logging

from src.workflows.context import WorkflowContext
from src.workflows.workflow_registry import Workflow, WorkflowStep

logger = logging.getLogger(__name__)


async def _poll_calendar_via_mcp(ctx: WorkflowContext) -> dict | None:
    """Try listing upcoming calendar events via Google Workspace MCP server."""
    from src.connectors.mcp_bridge import call_mcp_tool, is_mcp_tool

    for tool_name in (
        "google_workspace_calendar_list_events",
        "google-workspace_calendar_list_events",
        "calendar_list_events",
    ):
        if is_mcp_tool(tool_name):
            return await call_mcp_tool(
                tool_name,
                {"time_min": "now", "max_results": 5},
                user_id=ctx.user_id,
                workspace_id=ctx.workspace_id,
            )
    return None


async def find_next_meeting(ctx: WorkflowContext) -> dict:
    """Find the next upcoming calendar meeting — MCP first, integration fallback."""
    from datetime import datetime, timezone

    # Try MCP bridge
    mcp_result = await _poll_calendar_via_mcp(ctx)
    if mcp_result and mcp_result.get("status") == "ok":
        logger.info("Fetched calendar events via MCP bridge")
        return {
            "meeting": mcp_result.get("result"),
            "source": "mcp",
        }

    # Fallback to direct connector
    from src.connectors.base import CONNECTOR_REGISTRY

    cal_cls = CONNECTOR_REGISTRY.get("calendar")
    if not cal_cls:
        return {"meeting": None, "error": "No calendar source available"}

    connector = cal_cls(ctx.settings) if ctx.settings else cal_cls.__new__(cal_cls)

    events, _ = await connector.poll(
        user_id=ctx.user_id,
        cursor=None,
        credentials=ctx.credentials,
    )

    if not events:
        return {"meeting": None, "note": "No upcoming meetings found"}

    now = datetime.now(timezone.utc)
    upcoming = [evt for evt in events if evt.occurred_at and evt.occurred_at > now]

    if not upcoming:
        return {"meeting": None, "note": "No future meetings in poll results"}

    upcoming.sort(key=lambda e: e.occurred_at)
    meeting = upcoming[0]

    return {
        "meeting": {
            "entity_id": meeting.entity_id,
            "title": meeting.title,
            "summary": meeting.summary,
            "starts_at": meeting.occurred_at.isoformat() if meeting.occurred_at else None,
            "actor": meeting.actor,
            "raw_payload": meeting.raw_payload,
        },
        "source": "connector",
    }


async def gather_attendee_context(ctx: WorkflowContext) -> dict:
    """Look up attendee entities and related memories."""
    meeting = ctx.get("meeting")
    if not meeting:
        return {"attendees": [], "memories": []}

    services = ctx.services

    actor = meeting.get("actor", {})
    attendee_emails = []
    if actor and actor.get("email"):
        attendee_emails.append(actor["email"])

    attendees = []
    world_model = services.world_model if services else None
    if world_model and attendee_emails:
        for email in attendee_emails[:10]:
            entity = await world_model.find_entity(
                ctx.user_id, email, workspace_id=ctx.workspace_id
            )
            if entity:
                attendees.append(entity)

    memories = []
    memory_service = services.memory_service if services else None
    if memory_service:
        title = meeting.get("title", "")
        if title:
            memories = await memory_service.retrieve(
                user_id=ctx.user_id,
                query=title,
                max_results=5,
                workspace_id=ctx.workspace_id,
            )

    return {"attendees": attendees, "memories": memories}


async def generate_prep(ctx: WorkflowContext) -> dict:
    """Generate meeting prep document using the Presenter."""
    meeting = ctx.get("meeting")
    if not meeting:
        return {"prep": None, "note": "No meeting to prepare for"}

    services = ctx.services
    presenter = services.presenter if services else None
    if not presenter:
        return {"prep": None, "error": "Presenter service not available"}

    entity_id = meeting.get("entity_id", "")
    prep = await presenter.generate_meeting_prep(
        meeting_id=entity_id,
        user_id=ctx.user_id,
        next_meeting=not entity_id,
        workspace_id=ctx.workspace_id,
    )

    return {"prep": prep}


async def notify_user(ctx: WorkflowContext) -> dict:
    """Notify user that meeting prep is ready."""
    prep = ctx.get("prep")
    if not prep:
        return {"notified": False, "reason": "No prep generated"}

    services = ctx.services
    notifier = services.notifier if services else None
    if not notifier:
        return {"notified": False, "reason": "No notifier available"}

    meeting = ctx.get("meeting", {})
    title = meeting.get("title", "Upcoming meeting") if isinstance(meeting, dict) else "Meeting"

    try:
        await notifier.notify(
            user_id=ctx.user_id,
            notification_type="info_update",
            title=f"Meeting Prep: {title}",
            body=f"Prep ready for {title}",
            data={
                "meeting_id": meeting.get("entity_id") if isinstance(meeting, dict) else None,
                "prep": prep,
            },
        )
        return {"notified": True}
    except Exception:
        logger.warning("Meeting prep notification failed", exc_info=True)
        return {"notified": False, "reason": "Notification failed"}


meeting_prep_workflow = Workflow(
    name="meeting_prep",
    description="Find next meeting, gather attendee context, generate prep document, notify user",
    steps=[
        WorkflowStep(name="find_next_meeting", handler=find_next_meeting),
        WorkflowStep(name="gather_attendee_context", handler=gather_attendee_context),
        WorkflowStep(name="generate_prep", handler=generate_prep),
        WorkflowStep(name="notify_user", handler=notify_user),
    ],
    tags=["calendar", "meeting", "prep"],
)
