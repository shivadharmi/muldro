"""Recommendation surface detail tab builders."""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.ui import renderer as r
from src.ui.contracts import A2UIComponent, DetailSection, DetailTabResponse

from ._shared import (
    _empty_tab,
    _get_payload,
    _section,
    _truncate,
)

logger = logging.getLogger(__name__)


async def build_recommendation_overview(
    db: AsyncSession, surface: Any, **kwargs: Any
) -> DetailTabResponse:
    payload = _get_payload(surface)
    text_content = payload.get("response_preview", "")
    preview = payload.get("preview", {})
    title = preview.get("title", "") if isinstance(preview, dict) else ""

    children: list[A2UIComponent] = [
        r.text("rec_text", text_content or title or "No recommendation content.")
    ]
    return DetailTabResponse(
        tab_id="overview",
        sections=[_section("content", "Recommendation", children, collapsed=False)],
    )


async def build_recommendation_context(
    db: AsyncSession, surface: Any, **kwargs: Any
) -> DetailTabResponse:
    """Recommendation context — related memories."""
    from src.models.memory import Memory

    ws_id = getattr(surface, "workspace_id", None)
    if not ws_id:
        return _empty_tab("context", "No workspace context available.")

    # Fetch recent active memories as general context
    result = await db.execute(
        select(Memory)
        .where(
            Memory.workspace_id == ws_id,
            Memory.status == "active",
        )
        .order_by(Memory.last_accessed_at.desc().nullslast())
        .limit(10)
    )
    memories = list(result.scalars().all())

    if not memories:
        return _empty_tab("context", "No related memories found.")

    children = [
        r.memory_card(
            f"mem_{i}",
            mem.fact_text or "",
            mem.memory_type or "factual",
            source="memory",
            confidence=mem.confidence or 0.5,
        )
        for i, mem in enumerate(memories)
    ]
    return DetailTabResponse(
        tab_id="context",
        sections=[_section("memories", "Related Memories", children, collapsed=False)],
    )


async def build_recommendation_evidence(
    db: AsyncSession, surface: Any, **kwargs: Any
) -> DetailTabResponse:
    """Recommendation evidence — failed runs or open circuit breakers depending on title."""
    payload = _get_payload(surface)
    preview = payload.get("preview", {})
    title = (preview.get("title", "") if isinstance(preview, dict) else "").lower()

    sections: list[DetailSection] = []

    if "failed" in title or "fail" in title:
        from src.models.task_graph import TaskRun

        ws_id = getattr(surface, "workspace_id", None)
        if ws_id:
            cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
            result = await db.execute(
                select(TaskRun)
                .where(
                    TaskRun.workspace_id == ws_id,
                    TaskRun.status == "failed",
                    TaskRun.created_at >= cutoff,
                )
                .order_by(TaskRun.created_at.desc())
                .limit(10)
            )
            failed_runs = list(result.scalars().all())
            if failed_runs:
                children: list[A2UIComponent] = []
                for i, run in enumerate(failed_runs):
                    err_msg = ""
                    if run.error and isinstance(run.error, dict):
                        err_msg = run.error.get("message", str(run.error))
                    run_children: list[A2UIComponent] = [
                        r.badge(f"fr_{i}_st", "failed", variant="danger"),
                        r.text(f"fr_{i}_src", f"Source: {run.source or 'unknown'}"),
                    ]
                    if err_msg:
                        run_children.append(r.caption(f"fr_{i}_err", _truncate(str(err_msg), 150)))
                    children.append(r.row(f"fr_{i}", run_children))
                sections.append(_section("failures", f"Failed Runs ({len(failed_runs)})", children))

    if "source" in title or "failing" in title:
        from src.models.perception_state import PerceptionState

        ws_id = getattr(surface, "workspace_id", None)
        if ws_id:
            result = await db.execute(
                select(PerceptionState).where(
                    PerceptionState.workspace_id == ws_id,
                    PerceptionState.circuit_state == "open",
                )
            )
            open_sources = list(result.scalars().all())
            if open_sources:
                children = []
                for i, ps in enumerate(open_sources):
                    children.append(
                        r.row(
                            f"ps_{i}",
                            [
                                r.badge(f"ps_{i}_src", ps.source, variant="danger"),
                                r.text(
                                    f"ps_{i}_err",
                                    _truncate(ps.last_error or "No error details", 120),
                                ),
                                r.caption(
                                    f"ps_{i}_fail",
                                    f"Failures: {ps.consecutive_failures}",
                                ),
                            ],
                        )
                    )
                sections.append(
                    _section("circuits", f"Open Circuit Breakers ({len(open_sources)})", children)
                )

    if not sections:
        return _empty_tab("evidence", "No evidence data available.")
    return DetailTabResponse(tab_id="evidence", sections=sections)
