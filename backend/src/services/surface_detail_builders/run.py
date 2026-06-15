"""Run/Summary unified surface detail tab builders."""

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.ui.contracts import DetailTabResponse

from ._shared import (
    _empty_tab,
    _extract_run_id,
    _section,
    _truncate,
)

logger = logging.getLogger(__name__)


async def build_run_steps_tab(db: AsyncSession, surface: Any, **kwargs: Any) -> DetailTabResponse:
    """Steps tab for a run surface: ordered list with status, duration, output."""
    from src.models.task_graph import TaskRun, TaskStep
    from src.ui import units

    run_id = _extract_run_id(surface)
    if not run_id:
        # Run surfaces use id format run_{run_id}
        surface_id = getattr(surface, "surface_id", "") or ""
        if surface_id.startswith("run_"):
            run_id = surface_id.removeprefix("run_")
        elif surface_id.startswith("summary_"):
            run_id = surface_id.removeprefix("summary_")

    if not run_id:
        return _empty_tab("steps", "No linked run.")

    run = (await db.execute(select(TaskRun).where(TaskRun.run_id == run_id))).scalar_one_or_none()
    if not run:
        return _empty_tab("steps", f"Run {run_id[:16]}... not found.")

    steps = list(
        (
            await db.execute(
                select(TaskStep).where(TaskStep.run_id == run_id).order_by(TaskStep.step_order)
            )
        )
        .scalars()
        .all()
    )

    step_states = [
        {
            "step_id": s.step_id,
            "description": s.name or (s.input_data or {}).get("description", "") or s.step_id,
            "status": s.status or "pending",
            "output_summary": (
                _truncate(str((s.output_data or {}).get("result", "")), 240)
                if s.output_data
                else None
            ),
            "duration_ms": (
                int((s.completed_at - s.started_at).total_seconds() * 1000)
                if (s.started_at and s.completed_at)
                else None
            ),
        }
        for s in steps
    ]

    return DetailTabResponse(
        tab_id="steps",
        sections=[
            _section(
                "steps",
                f"Steps ({len(steps)})",
                [units.step_list(steps=step_states, run_id=run_id)],
                collapsed=False,
            )
        ],
    )


async def build_run_plan_tab(db: AsyncSession, surface: Any, **kwargs: Any) -> DetailTabResponse:
    """Plan tab: goal, reasoning, success criteria, priority, trigger from the linked Plan row."""
    from src.models.plans import Plan
    from src.models.task_graph import TaskRun
    from src.ui import units

    run_id = _extract_run_id(surface)
    if not run_id:
        surface_id = getattr(surface, "surface_id", "") or ""
        if surface_id.startswith("run_"):
            run_id = surface_id.removeprefix("run_")
        elif surface_id.startswith("summary_"):
            run_id = surface_id.removeprefix("summary_")

    if not run_id:
        return _empty_tab("plan", "No linked run.")

    run = (await db.execute(select(TaskRun).where(TaskRun.run_id == run_id))).scalar_one_or_none()
    if not run or not run.plan_id:
        return _empty_tab("plan", "Run has no linked plan.")

    plan = (await db.execute(select(Plan).where(Plan.plan_id == run.plan_id))).scalar_one_or_none()
    if not plan:
        return _empty_tab("plan", "Plan not found.")

    return DetailTabResponse(
        tab_id="plan",
        sections=[
            _section(
                "plan",
                "Plan",
                [
                    units.plan_summary(
                        goal=plan.goal or "",
                        reasoning=plan.reasoning or "",
                        success_criteria=plan.success_criteria or "",
                        priority=plan.priority or "",
                        trigger_type=plan.trigger_type or "",
                        run_id=run_id,
                    )
                ],
                collapsed=False,
            )
        ],
    )


async def build_run_events_tab(db: AsyncSession, surface: Any, **kwargs: Any) -> DetailTabResponse:
    """Events tab: RuntimeEvent timeline ordered by occurred_at."""
    from src.models.runtime_event import RuntimeEvent
    from src.ui import units

    run_id = _extract_run_id(surface)
    if not run_id:
        surface_id = getattr(surface, "surface_id", "") or ""
        if surface_id.startswith("run_"):
            run_id = surface_id.removeprefix("run_")
        elif surface_id.startswith("summary_"):
            run_id = surface_id.removeprefix("summary_")

    if not run_id:
        return _empty_tab("events", "No linked run.")

    events = list(
        (
            await db.execute(
                select(RuntimeEvent)
                .where(RuntimeEvent.run_id == run_id)
                .order_by(RuntimeEvent.occurred_at)
            )
        )
        .scalars()
        .all()
    )

    event_dicts = [
        {
            "timestamp": e.occurred_at.isoformat() if e.occurred_at else "",
            "event_type": e.event_type,
            "description": (e.payload or {}).get("summary", "") if e.payload else "",
        }
        for e in events
    ]

    return DetailTabResponse(
        tab_id="events",
        sections=[
            _section(
                "events",
                f"Events ({len(events)})",
                [units.event_timeline(events=event_dicts, run_id=run_id)],
                collapsed=False,
            )
        ],
    )


async def build_run_trace_tab(db: AsyncSession, surface: Any, **kwargs: Any) -> DetailTabResponse:
    """Trace tab: token/cost totals + per-agent breakdown.

    Uses the three-layer fallback from routes_history: trace_id JOIN,
    traces.run_id reverse lookup, then the task_runs rollup cache.
    """
    from src.models.task_graph import TaskRun
    from src.models.traces import ModelCall, Trace
    from src.ui import units

    run_id = _extract_run_id(surface)
    if not run_id:
        surface_id = getattr(surface, "surface_id", "") or ""
        if surface_id.startswith("run_"):
            run_id = surface_id.removeprefix("run_")
        elif surface_id.startswith("summary_"):
            run_id = surface_id.removeprefix("summary_")

    if not run_id:
        return _empty_tab("trace", "No linked run.")

    run = (await db.execute(select(TaskRun).where(TaskRun.run_id == run_id))).scalar_one_or_none()
    if not run:
        return _empty_tab("trace", "Run not found.")

    trace_row = None
    if run.trace_id:
        trace_row = (
            await db.execute(select(Trace).where(Trace.trace_id == run.trace_id))
        ).scalar_one_or_none()
    if trace_row is None:
        trace_row = (
            await db.execute(select(Trace).where(Trace.run_id == run.run_id))
        ).scalar_one_or_none()

    input_t = int((trace_row.total_input_tokens if trace_row else 0) or run.input_tokens or 0)
    output_t = int((trace_row.total_output_tokens if trace_row else 0) or run.output_tokens or 0)
    cost = float((trace_row.total_cost_usd if trace_row else 0) or run.cost_usd or 0.0)
    duration_ms = (
        trace_row.duration_ms
        if trace_row and trace_row.duration_ms
        else (
            int((run.completed_at - run.started_at).total_seconds() * 1000)
            if run.started_at and run.completed_at
            else None
        )
    )

    step_breakdown: list[dict[str, Any]] = []
    if trace_row is not None:
        calls = list(
            (await db.execute(select(ModelCall).where(ModelCall.trace_id == trace_row.trace_id)))
            .scalars()
            .all()
        )
        by_agent: dict[str, dict[str, Any]] = {}
        for c in calls:
            key = c.agent_name or "unknown"
            entry = by_agent.setdefault(
                key,
                {
                    "step_id": key,
                    "agent": key,
                    "calls": 0,
                    "tokens": 0,
                    "cost_usd": 0.0,
                    "duration_ms": 0,
                },
            )
            entry["calls"] += 1
            entry["tokens"] += int((c.input_tokens or 0) + (c.output_tokens or 0))
            entry["cost_usd"] = round(entry["cost_usd"] + float(c.cost_usd or 0), 6)
            entry["duration_ms"] += int(c.duration_ms or 0)
        step_breakdown = list(by_agent.values())

    return DetailTabResponse(
        tab_id="trace",
        sections=[
            _section(
                "trace",
                "Trace",
                [
                    units.trace_metrics(
                        input_tokens=input_t,
                        output_tokens=output_t,
                        cost_usd=cost,
                        duration_ms=duration_ms,
                        step_breakdown=step_breakdown or None,
                        run_id=run_id,
                    )
                ],
                collapsed=False,
            )
        ],
    )
