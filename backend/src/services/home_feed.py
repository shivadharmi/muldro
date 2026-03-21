"""Home feed service — aggregates data for GET /v1/home.

Pulls from briefings, approvals, traces, runs, observations, goals,
and runtime events to build a unified home screen response.
"""

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.approvals import Approval
from src.models.briefings import Briefing
from src.models.runtime_event import RuntimeEvent
from src.models.task_graph import TaskRun

logger = logging.getLogger(__name__)


class HomeFeedService:
    """Builds the home feed for a workspace."""

    def __init__(self, db: AsyncSession, workspace_id: str):
        self._db = db
        self._workspace_id = workspace_id

    async def build_home(self, since: datetime | None = None) -> dict:
        """Build the complete home feed response."""
        if since is None:
            since = datetime.now(timezone.utc) - timedelta(hours=24)

        priority_items = await self._get_priority_items()
        live_activity = await self._get_live_activity(limit=10)
        recent_intelligence = await self._get_recent_intelligence(since)

        recommended_actions = await self._get_recommended_actions()
        capability_health = await self._get_capability_health()

        return {
            "since_last_visit": since.isoformat(),
            "priority_items": priority_items,
            "live_activity": live_activity,
            "recommended_actions": recommended_actions,
            "recent_intelligence": recent_intelligence,
            "capability_health": capability_health,
        }

    async def _get_priority_items(self) -> list[dict]:
        """Get pending approvals and blocked runs as priority items."""
        items: list[dict] = []

        # Pending approvals
        result = await self._db.execute(
            select(Approval)
            .where(
                Approval.workspace_id == self._workspace_id,
                Approval.status == "pending",
            )
            .order_by(Approval.created_at.desc())
            .limit(10)
        )
        for apr in result.scalars().all():
            items.append(
                {
                    "item_type": "approval",
                    "item_id": apr.approval_id,
                    "title": apr.title or "Pending approval",
                    "priority": apr.risk_level or "medium",
                    "created_at": apr.created_at.isoformat() if apr.created_at else None,
                    "action_url": f"/approvals/{apr.approval_id}",
                }
            )

        # Blocked/awaiting runs
        result = await self._db.execute(
            select(TaskRun)
            .where(
                TaskRun.workspace_id == self._workspace_id,
                TaskRun.status.in_(["awaiting_approval", "blocked"]),
            )
            .order_by(TaskRun.created_at.desc())
            .limit(5)
        )
        for run in result.scalars().all():
            items.append(
                {
                    "item_type": "workflow_blocked",
                    "item_id": run.run_id,
                    "title": f"Run {run.run_id[:16]}... blocked",
                    "priority": "high",
                    "created_at": run.created_at.isoformat() if run.created_at else None,
                    "action_url": f"/runs/{run.run_id}",
                }
            )

        return items

    async def _get_live_activity(self, limit: int = 10) -> list[dict]:
        """Get recent runtime events as live activity."""
        result = await self._db.execute(
            select(RuntimeEvent)
            .where(RuntimeEvent.workspace_id == self._workspace_id)
            .order_by(RuntimeEvent.occurred_at.desc())
            .limit(limit)
        )
        events = result.scalars().all()
        return [
            {
                "event_type": e.event_type,
                "description": _event_description(e),
                "occurred_at": e.occurred_at.isoformat(),
                "run_id": e.run_id,
                "agent_name": (e.payload or {}).get("agent_name"),
            }
            for e in events
        ]

    async def _get_recent_intelligence(self, since: datetime) -> list[dict]:
        """Get recent briefings as intelligence items."""
        items: list[dict] = []

        result = await self._db.execute(
            select(Briefing)
            .where(
                Briefing.workspace_id == self._workspace_id,
                Briefing.created_at >= since,
            )
            .order_by(Briefing.created_at.desc())
            .limit(5)
        )
        for b in result.scalars().all():
            items.append(
                {
                    "item_type": "briefing",
                    "item_id": b.briefing_id,
                    "title": b.headline or f"Briefing {b.date}",
                    "summary": (b.full_text or "")[:200],
                    "created_at": b.created_at.isoformat() if b.created_at else None,
                }
            )

        return items

    async def _get_recommended_actions(self) -> list[dict]:
        """Derive recommended actions from pending approvals, blocked runs, stale data."""
        actions: list[dict] = []

        # Pending approvals → recommend reviewing
        pending_result = await self._db.execute(
            select(func.count(Approval.approval_id)).where(
                Approval.workspace_id == self._workspace_id,
                Approval.status == "pending",
            )
        )
        pending_count = pending_result.scalar() or 0
        if pending_count > 0:
            actions.append(
                {
                    "action_type": "review_approvals",
                    "title": (
                        f"Review {pending_count} pending approval"
                        f"{'s' if pending_count > 1 else ''}"
                    ),
                    "description": "Actions are waiting for your approval before they can proceed.",
                    "priority": "high",
                    "action_url": "/approvals?status=pending",
                }
            )

        # Blocked runs → recommend unblocking
        blocked_result = await self._db.execute(
            select(func.count(TaskRun.run_id)).where(
                TaskRun.workspace_id == self._workspace_id,
                TaskRun.status.in_(["blocked", "awaiting_approval"]),
            )
        )
        blocked_count = blocked_result.scalar() or 0
        if blocked_count > 0:
            actions.append(
                {
                    "action_type": "unblock_runs",
                    "title": (
                        f"Unblock {blocked_count} stalled workflow"
                        f"{'s' if blocked_count > 1 else ''}"
                    ),
                    "description": "Workflows are paused and need attention to continue.",
                    "priority": "high",
                    "action_url": "/workflows?status=blocked",
                }
            )

        # Failed runs in last 24h → recommend investigating
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        failed_result = await self._db.execute(
            select(func.count(TaskRun.run_id)).where(
                TaskRun.workspace_id == self._workspace_id,
                TaskRun.status == "failed",
                TaskRun.updated_at >= cutoff,
            )
        )
        failed_count = failed_result.scalar() or 0
        if failed_count > 0:
            actions.append(
                {
                    "action_type": "investigate_failures",
                    "title": (
                        f"Investigate {failed_count} failed run"
                        f"{'s' if failed_count > 1 else ''}"
                    ),
                    "description": "Recent workflow failures may need your attention.",
                    "priority": "medium",
                    "action_url": "/workflows?status=failed",
                }
            )

        return actions

    async def _get_capability_health(self) -> list[dict]:
        """Delegate to CapabilityHealthService for capability status."""
        try:
            from src.services.capability_health import CapabilityHealthService

            svc = CapabilityHealthService(self._db, self._workspace_id)
            report = await svc.get_health_report()
            return [
                {
                    "family": f.family,
                    "status": f.status,
                    "provider": f.provider,
                    "last_activity_at": (
                        f.last_activity_at.isoformat() if f.last_activity_at else None
                    ),
                    "capabilities_available": f.capabilities_available,
                    "capabilities_total": f.capabilities_total,
                    "message": f.message,
                }
                for f in report.families
            ]
        except Exception:
            logger.warning("Failed to get capability health", exc_info=True)
            return []


def _event_description(event: RuntimeEvent) -> str:
    """Generate a human-readable description for a runtime event."""
    payload = event.payload or {}
    descriptions = {
        "route_selected": f"Route selected: {payload.get('route_name', 'unknown')}",
        "agent_started": f"Agent started: {payload.get('agent_name', 'unknown')}",
        "tool_call_started": f"Tool call: {payload.get('tool_name', 'unknown')}",
        "tool_call_completed": f"Tool completed: {payload.get('tool_name', 'unknown')}",
        "approval_requested": f"Approval needed: {payload.get('title', 'action')}",
        "artifact_created": f"Artifact created: {payload.get('title', 'item')}",
        "fallback_triggered": f"Fallback triggered for {payload.get('capability', 'unknown')}",
        "run_completed": "Run completed successfully",
        "run_failed": f"Run failed: {payload.get('error', 'unknown error')}",
    }
    return descriptions.get(event.event_type, event.event_type)
