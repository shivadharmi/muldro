"""Plan surface detail tab builders."""

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.services.execution_state import TERMINAL_SUCCESS
from src.ui import renderer as r
from src.ui.contracts import A2UIComponent, DetailSection, DetailTabResponse

from ._shared import (
    _empty_tab,
    _extract_run_id,
    _format_ts,
    _get_step_desc,
    _section,
    _truncate,
)

logger = logging.getLogger(__name__)


async def _load_context_pack(db, run) -> dict:
    """Dual-read (Step 5, D-C4): prefer RunDetailStore, fall back to the run's old
    context_pack_json column for a run with no detail row yet (pre-cutover gap).
    Post-contract (column dropped) the getattr fallback is simply None -> {}."""
    if run is None:
        return {}
    from src.services.run_detail_store import RunDetailStore

    pack = await RunDetailStore(db).get_context_pack(run.run_id)
    if pack is not None:
        return pack
    return getattr(run, "context_pack_json", None) or {}


async def build_plan_overview(db: AsyncSession, surface: Any, **kwargs: Any) -> DetailTabResponse:
    """Plan overview — TaskRun + TaskSteps with statuses."""
    from src.models.task_graph import TaskRun, TaskStep

    run_id = _extract_run_id(surface)
    if not run_id:
        return _empty_tab("overview", "No linked execution run.")

    run_result = await db.execute(select(TaskRun).where(TaskRun.run_id == run_id))
    run = run_result.scalar_one_or_none()
    if not run:
        return _empty_tab("overview", f"Run {run_id[:16]}... not found.")

    steps_result = await db.execute(
        select(TaskStep).where(TaskStep.run_id == run_id).order_by(TaskStep.step_order)
    )
    steps = list(steps_result.scalars().all())

    run_children: list[A2UIComponent] = [
        r.badge("run_status", run.status or "unknown"),
        r.text("run_source", f"Source: {run.source or 'unknown'}"),
    ]
    if run.started_at:
        run_children.append(r.caption("run_started", f"Started: {_format_ts(run.started_at)}"))
    if run.completed_at:
        run_children.append(r.caption("run_done", f"Completed: {_format_ts(run.completed_at)}"))

    step_children: list[A2UIComponent] = []
    for i, step in enumerate(steps):
        variant = "success" if step.status in TERMINAL_SUCCESS else "default"
        step_children.append(
            r.row(
                f"step_{i}",
                [
                    r.badge(f"step_{i}_st", step.status or "pending", variant=variant),
                    r.text(f"step_{i}_name", step.name or step.step_type or f"Step {i + 1}"),
                    r.caption(f"step_{i}_desc", _truncate(_get_step_desc(step), 100)),
                ],
            )
        )

    completed = sum(1 for s in steps if s.status in TERMINAL_SUCCESS)
    total = len(steps)
    sections = [_section("summary", "Run Summary", run_children, collapsed=False)]
    if step_children:
        sections.append(
            _section("steps", f"Steps ({completed}/{total})", step_children, collapsed=False)
        )
    return DetailTabResponse(tab_id="overview", sections=sections)


async def build_plan_context(db: AsyncSession, surface: Any, **kwargs: Any) -> DetailTabResponse:
    """Plan context — memories, entities from the context pack."""
    from src.models.task_graph import TaskRun

    run_id = _extract_run_id(surface)
    sections: list[DetailSection] = []

    if run_id:
        run_result = await db.execute(select(TaskRun).where(TaskRun.run_id == run_id))
        run = run_result.scalar_one_or_none()
        ctx = await _load_context_pack(db, run)

        if ctx.get("memories"):
            mem_children = [
                r.memory_card(
                    f"ctx_mem_{i}",
                    m.get("fact_text", ""),
                    m.get("memory_type", "factual"),
                    source=m.get("source", ""),
                )
                for i, m in enumerate(ctx["memories"][:10])
            ]
            sections.append(_section("memories", "Related Memories", mem_children))

        if ctx.get("entities"):
            ent_children = [
                r.entity_card(
                    f"ctx_ent_{i}",
                    e.get("name", ""),
                    e.get("entity_type", "unknown"),
                )
                for i, e in enumerate(ctx["entities"][:10])
            ]
            sections.append(_section("entities", "Related Entities", ent_children))

    if not sections:
        return _empty_tab("context", "No context data available for this plan.")
    return DetailTabResponse(tab_id="context", sections=sections)


async def build_plan_execution(db: AsyncSession, surface: Any, **kwargs: Any) -> DetailTabResponse:
    """Plan execution trace — step-by-step tool calls, results, timings."""
    from src.models.task_graph import TaskStep

    run_id = _extract_run_id(surface)
    if not run_id:
        return _empty_tab("execution", "No linked execution run.")

    steps_result = await db.execute(
        select(TaskStep).where(TaskStep.run_id == run_id).order_by(TaskStep.step_order)
    )
    steps = list(steps_result.scalars().all())
    if not steps:
        return _empty_tab("execution", "No execution steps recorded.")

    trace_events = []
    for step in steps:
        event: dict[str, str] = {
            "label": step.name or step.step_type or "step",
            "status": step.status or "pending",
            "description": _truncate(_get_step_desc(step), 80),
        }
        if step.output_data and isinstance(step.output_data, dict):
            event["result"] = _truncate(str(step.output_data), 200)
        trace_events.append(event)

    return DetailTabResponse(
        tab_id="execution",
        sections=[
            _section(
                "trace",
                "Execution Trace",
                [r.execution_trace("exec_trace", trace_events)],
                collapsed=False,
            )
        ],
    )
