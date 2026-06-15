"""List-style surface detail tab builders (checklist, comparison, timeline, table, activity)."""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.ui import renderer as r
from src.ui.contracts import A2UIComponent, DetailTabResponse

from ._shared import (
    _empty_tab,
    _extract_run_id,
    _format_ts,
    _get_payload,
    _section,
    _truncate,
)
from .briefing import build_briefing_events
from .plan import build_plan_context

logger = logging.getLogger(__name__)


async def build_checklist_items(db: AsyncSession, surface: Any, **kwargs: Any) -> DetailTabResponse:
    """Checklist items — structured items from payload or TaskSteps fallback."""
    payload = _get_payload(surface)
    surface_data = payload.get("surface_data", {})
    items = surface_data.get("items", []) if isinstance(surface_data, dict) else []

    if items:
        children: list[A2UIComponent] = []
        for i, item in enumerate(items):
            status = item.get("status", "pending") if isinstance(item, dict) else "pending"
            title = item.get("title", str(item)) if isinstance(item, dict) else str(item)
            variant = "success" if status == "completed" else "default"
            children.append(
                r.row(
                    f"cl_item_{i}",
                    [
                        r.badge(f"cl_item_{i}_st", status, variant=variant),
                        r.text(f"cl_item_{i}_title", _truncate(title, 100)),
                    ],
                )
            )
        return DetailTabResponse(
            tab_id="items",
            sections=[_section("items", f"Items ({len(items)})", children, collapsed=False)],
        )

    # Fallback: use TaskSteps from linked run
    from src.models.task_graph import TaskStep

    run_id = _extract_run_id(surface)
    if not run_id:
        return _empty_tab("items", "No checklist items available.")

    steps_result = await db.execute(
        select(TaskStep).where(TaskStep.run_id == run_id).order_by(TaskStep.step_order)
    )
    steps = list(steps_result.scalars().all())
    if not steps:
        return _empty_tab("items", "No checklist items available.")

    children = []
    for i, step in enumerate(steps):
        variant = "success" if step.status == "completed" else "default"
        children.append(
            r.row(
                f"cl_step_{i}",
                [
                    r.badge(f"cl_step_{i}_st", step.status or "pending", variant=variant),
                    r.text(f"cl_step_{i}_name", step.name or step.step_type or f"Step {i + 1}"),
                ],
            )
        )
    return DetailTabResponse(
        tab_id="items",
        sections=[_section("items", f"Items ({len(steps)})", children, collapsed=False)],
    )


async def build_checklist_context(
    db: AsyncSession, surface: Any, **kwargs: Any
) -> DetailTabResponse:
    """Checklist context — delegates to plan context builder."""
    result = await build_plan_context(db, surface, **kwargs)
    return DetailTabResponse(tab_id="context", sections=result.sections)


# ── Comparison builders ────────────────────────────────────────


async def build_comparison_options(
    db: AsyncSession, surface: Any, **kwargs: Any
) -> DetailTabResponse:
    """Comparison options — each option as a card with pros and cons."""
    payload = _get_payload(surface)
    surface_data = payload.get("surface_data", {})
    options = surface_data.get("options", []) if isinstance(surface_data, dict) else []

    if options:
        children: list[A2UIComponent] = []
        for i, opt in enumerate(options):
            if not isinstance(opt, dict):
                continue
            name = opt.get("name", f"Option {i + 1}")
            desc = opt.get("description", "")
            pros = opt.get("pros", [])
            cons = opt.get("cons", [])

            card_children: list[A2UIComponent] = [
                r.text(f"opt_{i}_name", name),
            ]
            if desc:
                card_children.append(r.caption(f"opt_{i}_desc", desc))
            for j, pro in enumerate(pros):
                card_children.append(r.badge(f"opt_{i}_pro_{j}", str(pro), variant="success"))
            for j, con in enumerate(cons):
                card_children.append(r.badge(f"opt_{i}_con_{j}", str(con), variant="danger"))
            children.append(r.card(f"opt_{i}", card_children))

        return DetailTabResponse(
            tab_id="options",
            sections=[_section("options", f"Options ({len(options)})", children, collapsed=False)],
        )

    # Fallback to response_preview
    preview = payload.get("response_preview", "")
    return DetailTabResponse(
        tab_id="options",
        sections=[
            _section(
                "options",
                "Options",
                [r.text("opt_fallback", preview or "No comparison data available.")],
                collapsed=False,
            )
        ],
    )


async def build_comparison_criteria(
    db: AsyncSession, surface: Any, **kwargs: Any
) -> DetailTabResponse:
    """Comparison criteria — renders criteria as badge list."""
    payload = _get_payload(surface)
    surface_data = payload.get("surface_data", {})
    criteria = surface_data.get("criteria", []) if isinstance(surface_data, dict) else []

    if not criteria:
        return _empty_tab("criteria", "No criteria defined.")

    children: list[A2UIComponent] = [
        r.badge(f"crit_{i}", str(c), variant="default") for i, c in enumerate(criteria)
    ]
    return DetailTabResponse(
        tab_id="criteria",
        sections=[_section("criteria", "Criteria", children, collapsed=False)],
    )


# ── Timeline builders ──────────────────────────────────────────


async def build_timeline_events(db: AsyncSession, surface: Any, **kwargs: Any) -> DetailTabResponse:
    """Timeline events — renders events via r.timeline() or falls back to briefing events."""
    payload = _get_payload(surface)
    surface_data = payload.get("surface_data", {})
    events = surface_data.get("events", []) if isinstance(surface_data, dict) else []

    if events:
        return DetailTabResponse(
            tab_id="events",
            sections=[
                _section(
                    "timeline",
                    f"Events ({len(events)})",
                    [r.timeline("tl_events", events)],
                    collapsed=False,
                )
            ],
        )

    # Fallback to briefing events builder
    result = await build_briefing_events(db, surface, **kwargs)
    return DetailTabResponse(tab_id="events", sections=result.sections)


async def build_timeline_context(
    db: AsyncSession, surface: Any, **kwargs: Any
) -> DetailTabResponse:
    """Timeline context — delegates to plan context builder."""
    result = await build_plan_context(db, surface, **kwargs)
    return DetailTabResponse(tab_id="context", sections=result.sections)


# ── Table builders ─────────────────────────────────────────────


async def build_table_data(db: AsyncSession, surface: Any, **kwargs: Any) -> DetailTabResponse:
    """Table data — renders columns and rows via r.table()."""
    payload = _get_payload(surface)
    surface_data = payload.get("surface_data", {})

    columns = surface_data.get("columns", []) if isinstance(surface_data, dict) else []
    rows = surface_data.get("rows", []) if isinstance(surface_data, dict) else []

    if columns and rows:
        return DetailTabResponse(
            tab_id="data",
            sections=[
                _section(
                    "table",
                    f"Data ({len(rows)} rows)",
                    [r.table("tbl_data", columns, rows)],
                    collapsed=False,
                )
            ],
        )

    # Fallback to response_preview
    preview = payload.get("response_preview", "")
    return DetailTabResponse(
        tab_id="data",
        sections=[
            _section(
                "table",
                "Data",
                [r.text("tbl_fallback", preview or "No table data available.")],
                collapsed=False,
            )
        ],
    )


async def build_table_sources(db: AsyncSession, surface: Any, **kwargs: Any) -> DetailTabResponse:
    """Table sources — TaskSteps for the linked run with step types and timing."""
    from src.models.task_graph import TaskStep

    run_id = _extract_run_id(surface)
    if not run_id:
        return _empty_tab("sources", "No linked run for source lookup.")

    steps_result = await db.execute(
        select(TaskStep).where(TaskStep.run_id == run_id).order_by(TaskStep.step_order)
    )
    steps = list(steps_result.scalars().all())
    if not steps:
        return _empty_tab("sources", "No source steps found.")

    children: list[A2UIComponent] = []
    for i, step in enumerate(steps):
        step_children: list[A2UIComponent] = [
            r.badge(f"ts_{i}_type", step.step_type or "unknown"),
            r.text(f"ts_{i}_name", step.name or f"Step {i + 1}"),
        ]
        if step.started_at:
            step_children.append(
                r.caption(f"ts_{i}_start", f"Started: {_format_ts(step.started_at)}")
            )
        if step.completed_at:
            step_children.append(
                r.caption(f"ts_{i}_done", f"Completed: {_format_ts(step.completed_at)}")
            )
        children.append(r.row(f"ts_{i}", step_children))

    return DetailTabResponse(
        tab_id="sources",
        sections=[_section("sources", f"Source Steps ({len(steps)})", children, collapsed=False)],
    )


# ── Activity builders ──────────────────────────────────────────


async def build_activity_runs(db: AsyncSession, surface: Any, **kwargs: Any) -> DetailTabResponse:
    """Activity runs — recent TaskRuns for the workspace (last 24h)."""
    ws_id = getattr(surface, "workspace_id", None)
    if not ws_id:
        return _empty_tab("runs", "No workspace context for activity lookup.")

    from src.models.task_graph import TaskRun

    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    result = await db.execute(
        select(TaskRun)
        .where(TaskRun.workspace_id == ws_id, TaskRun.created_at >= cutoff)
        .order_by(TaskRun.created_at.desc())
        .limit(20)
    )
    runs = list(result.scalars().all())

    if not runs:
        return _empty_tab("runs", "No runs in the last 24 hours.")

    children: list[A2UIComponent] = []
    for i, run in enumerate(runs):
        variant = "success" if run.status == "completed" else "default"
        if run.status == "failed":
            variant = "danger"
        children.append(
            r.row(
                f"run_{i}",
                [
                    r.badge(f"run_{i}_st", run.status or "pending", variant=variant),
                    r.text(f"run_{i}_src", run.source or "unknown"),
                    r.caption(f"run_{i}_time", _format_ts(run.created_at)),
                ],
            )
        )

    return DetailTabResponse(
        tab_id="runs",
        sections=[_section("runs", f"Recent Runs ({len(runs)})", children, collapsed=False)],
    )


async def build_activity_stats(db: AsyncSession, surface: Any, **kwargs: Any) -> DetailTabResponse:
    """Activity stats — aggregated run counts for the workspace."""
    ws_id = getattr(surface, "workspace_id", None)
    if not ws_id:
        return _empty_tab("stats", "No workspace context for stats.")

    from sqlalchemy import func

    from src.models.task_graph import TaskRun

    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    base_filter = [TaskRun.workspace_id == ws_id, TaskRun.created_at >= cutoff]

    total_result = await db.execute(select(func.count(TaskRun.run_id)).where(*base_filter))
    total = total_result.scalar() or 0

    completed_result = await db.execute(
        select(func.count(TaskRun.run_id)).where(*base_filter, TaskRun.status == "completed")
    )
    completed = completed_result.scalar() or 0

    failed_result = await db.execute(
        select(func.count(TaskRun.run_id)).where(*base_filter, TaskRun.status == "failed")
    )
    failed = failed_result.scalar() or 0

    children: list[A2UIComponent] = [
        r.metric("stat_total", "Total Runs (24h)", total),
        r.metric("stat_completed", "Completed", completed),
        r.metric("stat_failed", "Failed", failed),
    ]
    if total > 0:
        pct = round((completed / total) * 100, 1)
        children.append(r.progress("stat_success_rate", pct, label=f"Success Rate: {pct}%"))

    return DetailTabResponse(
        tab_id="stats",
        sections=[_section("stats", "Run Statistics", children, collapsed=False)],
    )
