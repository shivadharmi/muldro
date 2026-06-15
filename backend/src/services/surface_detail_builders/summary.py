"""Summary surface detail tab builders."""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.ui import renderer as r
from src.ui.contracts import A2UIComponent, DetailSection, DetailTabResponse

from ._shared import (
    _empty_tab,
    _extract_run_id,
    _format_ts,
    _get_payload,
    _section,
    _truncate,
)

logger = logging.getLogger(__name__)


async def build_summary_overview(
    db: AsyncSession, surface: Any, **kwargs: Any
) -> DetailTabResponse:
    payload = _get_payload(surface)
    text_content = payload.get("response_preview", "") or ""
    children: list[A2UIComponent] = [
        r.text("summary_text", text_content or "No summary content available.")
    ]
    return DetailTabResponse(
        tab_id="overview",
        sections=[_section("content", "Summary", children, collapsed=False)],
    )


async def build_summary_sources(db: AsyncSession, surface: Any, **kwargs: Any) -> DetailTabResponse:
    """Summary sources — recent perception events from this workspace."""
    from src.models.events import NormalizedEvent

    run_id = _extract_run_id(surface)
    # Try to get workspace_id from surface
    ws_id = getattr(surface, "workspace_id", None)
    if not ws_id:
        payload = _get_payload(surface)
        ws_id = payload.get("workspace_id")

    if not ws_id and not run_id:
        return _empty_tab("sources", "No source data available.")

    # Fetch recent events (last 24h, up to 20)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    query = (
        select(NormalizedEvent)
        .where(NormalizedEvent.occurred_at >= cutoff)
        .order_by(NormalizedEvent.occurred_at.desc())
        .limit(20)
    )
    if ws_id:
        query = query.where(NormalizedEvent.workspace_id == ws_id)

    result = await db.execute(query)
    events = list(result.scalars().all())

    if not events:
        return _empty_tab("sources", "No recent perception events.")

    event_children: list[A2UIComponent] = []
    for i, evt in enumerate(events):
        event_children.append(
            r.row(
                f"evt_{i}",
                [
                    r.badge(f"evt_{i}_src", evt.source or "unknown"),
                    r.text(f"evt_{i}_title", _truncate(evt.title or evt.event_type or "", 80)),
                    r.caption(f"evt_{i}_time", _format_ts(evt.occurred_at)),
                ],
            )
        )

    return DetailTabResponse(
        tab_id="sources",
        sections=[
            _section("events", f"Recent Events ({len(events)})", event_children, collapsed=False)
        ],
    )


async def build_summary_context(db: AsyncSession, surface: Any, **kwargs: Any) -> DetailTabResponse:
    """Summary context — memories that may have informed the summary."""
    run_id = _extract_run_id(surface)
    if not run_id:
        return _empty_tab("context", "No linked run for context lookup.")

    # Check context pack on the run
    from src.models.task_graph import TaskRun

    run_result = await db.execute(select(TaskRun).where(TaskRun.run_id == run_id))
    run = run_result.scalar_one_or_none()
    ctx = (run.context_pack_json if run else None) or {}

    sections: list[DetailSection] = []
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

    if not sections:
        return _empty_tab("context", "No context data available.")
    return DetailTabResponse(tab_id="context", sections=sections)
