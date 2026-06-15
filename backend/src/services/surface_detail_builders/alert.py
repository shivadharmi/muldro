"""Alert surface detail tab builders."""

import logging
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

logger = logging.getLogger(__name__)


async def build_alert_overview(db: AsyncSession, surface: Any, **kwargs: Any) -> DetailTabResponse:
    """Alert overview — run details for blocked/priority alerts."""
    from src.models.task_graph import TaskRun

    run_id = _extract_run_id(surface)
    children: list[A2UIComponent] = []

    if run_id:
        result = await db.execute(select(TaskRun).where(TaskRun.run_id == run_id))
        run = result.scalar_one_or_none()
        if run:
            children.append(r.badge("alert_status", run.status or "unknown", variant="warning"))
            children.append(r.text("alert_source", f"Source: {run.source or 'unknown'}"))
            if run.error and isinstance(run.error, dict):
                err_msg = run.error.get("message", str(run.error))
                children.append(
                    r.alert("alert_err", _truncate(str(err_msg), 200), severity="error")
                )
            if run.started_at:
                children.append(
                    r.caption("alert_started", f"Started: {_format_ts(run.started_at)}")
                )

    if not children:
        # Fallback to preview data
        payload = _get_payload(surface)
        preview = payload.get("preview", {})
        title = preview.get("title", "Alert") if isinstance(preview, dict) else "Alert"
        subtitle = preview.get("subtitle", "") if isinstance(preview, dict) else ""
        children.append(r.text("alert_title", title))
        if subtitle:
            children.append(r.caption("alert_detail", subtitle))

    return DetailTabResponse(
        tab_id="overview",
        sections=[_section("details", "Alert Details", children, collapsed=False)],
    )


async def build_alert_diagnostics(
    db: AsyncSession, surface: Any, **kwargs: Any
) -> DetailTabResponse:
    """Alert diagnostics — failed/blocked/timed_out steps with error details."""
    from src.models.task_graph import TaskStep

    run_id = _extract_run_id(surface)
    if not run_id:
        return _empty_tab("diagnostics", "No linked run for diagnostics.")

    steps_result = await db.execute(
        select(TaskStep)
        .where(
            TaskStep.run_id == run_id,
            TaskStep.status.in_(["failed", "blocked", "timed_out"]),
        )
        .order_by(TaskStep.step_order)
    )
    steps = list(steps_result.scalars().all())

    if not steps:
        return _empty_tab("diagnostics", "No failed or blocked steps found.")

    children: list[A2UIComponent] = []
    for i, step in enumerate(steps):
        variant = "danger" if step.status == "failed" else "warning"
        step_children: list[A2UIComponent] = [
            r.badge(f"diag_{i}_st", step.status or "unknown", variant=variant),
            r.text(f"diag_{i}_name", step.name or step.step_type or f"Step {i + 1}"),
        ]
        if step.error and isinstance(step.error, dict):
            err_msg = step.error.get("message", str(step.error))
            step_children.append(
                r.alert(f"diag_{i}_err", _truncate(str(err_msg), 200), severity="error")
            )
        if step.started_at:
            step_children.append(
                r.caption(f"diag_{i}_start", f"Started: {_format_ts(step.started_at)}")
            )
        children.append(r.row(f"diag_{i}", step_children))

    return DetailTabResponse(
        tab_id="diagnostics",
        sections=[_section("diag", f"Problem Steps ({len(steps)})", children, collapsed=False)],
    )
