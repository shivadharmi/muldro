"""Runtime projection service — derives live system state from TaskRun/TaskStep/events.

Provides the read-model for:
- Active runs and their current stage
- Blocked runs awaiting approval or input
- Agent workload distribution
- Route quality metrics
"""

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.runtime_event import RuntimeEvent
from src.models.task_graph import TaskRun, TaskStep
from src.models.traces import Trace

logger = logging.getLogger(__name__)


class RuntimeProjectionService:
    """Derives runtime projections from existing data models."""

    def __init__(self, db: AsyncSession, workspace_id: str):
        self._db = db
        self._workspace_id = workspace_id

    async def get_active_runs(self, limit: int = 20) -> list[dict]:
        """Return currently active runs with status and progress."""
        result = await self._db.execute(
            select(TaskRun)
            .where(
                TaskRun.workspace_id == self._workspace_id,
                TaskRun.status.in_(["running", "pending", "awaiting_approval", "paused"]),
            )
            .order_by(TaskRun.created_at.desc())
            .limit(limit)
        )
        runs = result.scalars().all()

        active = []
        for run in runs:
            steps_result = await self._db.execute(
                select(TaskStep).where(TaskStep.run_id == run.run_id)
            )
            steps = steps_result.scalars().all()
            total = len(steps)
            completed = sum(1 for s in steps if s.status == "completed")
            blocking_step = next(
                (s for s in steps if s.status in ("awaiting_approval", "blocked")), None
            )

            active.append(
                {
                    "run_id": run.run_id,
                    "plan_id": run.plan_id,
                    "status": run.status,
                    "created_at": run.created_at.isoformat() if run.created_at else None,
                    "total_steps": total,
                    "completed_steps": completed,
                    "progress_pct": round(completed / total * 100) if total else 0,
                    "blocking_step_id": blocking_step.step_id if blocking_step else None,
                    "blocking_reason": blocking_step.status if blocking_step else None,
                }
            )
        return active

    async def get_blocked_runs(self) -> list[dict]:
        """Return runs that are blocked or awaiting approval."""
        result = await self._db.execute(
            select(TaskRun)
            .where(
                TaskRun.workspace_id == self._workspace_id,
                TaskRun.status.in_(["awaiting_approval", "blocked", "paused"]),
            )
            .order_by(TaskRun.created_at.desc())
        )
        runs = result.scalars().all()

        blocked = []
        for run in runs:
            steps_result = await self._db.execute(
                select(TaskStep).where(
                    TaskStep.run_id == run.run_id,
                    TaskStep.status.in_(["awaiting_approval", "blocked"]),
                )
            )
            blocking_steps = steps_result.scalars().all()

            blocked.append(
                {
                    "run_id": run.run_id,
                    "status": run.status,
                    "blocking_steps": [
                        {
                            "step_id": s.step_id,
                            "status": s.status,
                            "action": s.action if hasattr(s, "action") else None,
                        }
                        for s in blocking_steps
                    ],
                }
            )
        return blocked

    async def get_agent_workload(self) -> list[dict]:
        """Return workload per agent based on recent runs."""
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)

        result = await self._db.execute(
            select(
                Trace.agent_name,
                func.count(Trace.trace_id).label("call_count"),
                func.avg(Trace.duration_ms).label("avg_duration_ms"),
            )
            .where(
                Trace.workspace_id == self._workspace_id,
                Trace.created_at >= cutoff,
                Trace.agent_name.isnot(None),
            )
            .group_by(Trace.agent_name)
            .order_by(func.count(Trace.trace_id).desc())
        )

        workloads = []
        for row in result.all():
            workloads.append(
                {
                    "agent_name": row.agent_name,
                    "call_count_24h": row.call_count,
                    "avg_duration_ms": round(float(row.avg_duration_ms or 0), 1),
                }
            )
        return workloads

    async def get_runtime_summary(self) -> dict:
        """Aggregate runtime summary for the workspace."""
        active_runs = await self.get_active_runs(limit=100)
        blocked_runs = await self.get_blocked_runs()
        agent_workload = await self.get_agent_workload()

        # Recent completions
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        completed_result = await self._db.execute(
            select(func.count(TaskRun.run_id)).where(
                TaskRun.workspace_id == self._workspace_id,
                TaskRun.status == "completed",
                TaskRun.updated_at >= cutoff,
            )
        )
        completed_24h = completed_result.scalar() or 0

        failed_result = await self._db.execute(
            select(func.count(TaskRun.run_id)).where(
                TaskRun.workspace_id == self._workspace_id,
                TaskRun.status == "failed",
                TaskRun.updated_at >= cutoff,
            )
        )
        failed_24h = failed_result.scalar() or 0

        return {
            "active_runs": len(active_runs),
            "blocked_runs": len(blocked_runs),
            "completed_24h": completed_24h,
            "failed_24h": failed_24h,
            "agents_active": len([w for w in agent_workload if w["call_count_24h"] > 0]),
            "top_agents": agent_workload[:5],
        }

    async def get_recent_events(
        self, event_types: list[str] | None = None, limit: int = 50
    ) -> list[dict]:
        """Return recent runtime events."""
        stmt = (
            select(RuntimeEvent)
            .where(RuntimeEvent.workspace_id == self._workspace_id)
            .order_by(RuntimeEvent.occurred_at.desc())
            .limit(limit)
        )
        if event_types:
            stmt = stmt.where(RuntimeEvent.event_type.in_(event_types))

        result = await self._db.execute(stmt)
        events = result.scalars().all()
        return [
            {
                "event_id": e.event_id,
                "run_id": e.run_id,
                "step_id": e.step_id,
                "event_type": e.event_type,
                "occurred_at": e.occurred_at.isoformat(),
                "payload": e.payload or {},
            }
            for e in events
        ]

    async def emit_event(
        self,
        event_type: str,
        run_id: str | None = None,
        step_id: str | None = None,
        payload: dict | None = None,
    ) -> RuntimeEvent:
        """Create and persist a runtime event."""
        event = RuntimeEvent(
            workspace_id=self._workspace_id,
            run_id=run_id,
            step_id=step_id,
            event_type=event_type,
            payload=payload or {},
        )
        self._db.add(event)
        await self._db.flush()
        return event
