"""Briefing surface detail tab builders."""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.ui import renderer as r
from src.ui.contracts import A2UIComponent, DetailSection, DetailTabResponse

from ._shared import (
    _empty_tab,
    _format_ts,
    _resolve_briefing,
    _section,
    _truncate,
)

_NO_BRIEFING_YET = "No briefing has been generated yet today."

logger = logging.getLogger(__name__)


async def build_briefing_priorities(
    db: AsyncSession, surface: Any, **kwargs: Any
) -> DetailTabResponse:
    briefing, had_id = await _resolve_briefing(surface, db)
    if not briefing:
        return _empty_tab(
            "priorities",
            "Briefing not found." if had_id else _NO_BRIEFING_YET,
        )

    priorities = briefing.top_priorities or []
    children: list[A2UIComponent] = []
    for i, p in enumerate(priorities):
        title = p.get("title", "") if isinstance(p, dict) else str(p)
        why = p.get("why", "") if isinstance(p, dict) else ""
        children.append(r.text(f"pri_{i}_title", title))
        if why:
            children.append(r.markdown(f"pri_{i}_why", why))
        if i < len(priorities) - 1:
            children.append(r.divider(f"pri_{i}_div"))

    if not children:
        return _empty_tab("priorities", "No priorities in today's briefing.")
    return DetailTabResponse(
        tab_id="priorities",
        sections=[_section("priorities", "Top Priorities", children, collapsed=False)],
    )


async def build_briefing_events(db: AsyncSession, surface: Any, **kwargs: Any) -> DetailTabResponse:
    """Briefing events — recent perception events from the last 24h."""
    from src.models.events import NormalizedEvent

    # Get workspace_id from surface or resolve via the linked/most-recent briefing
    ws_id = getattr(surface, "workspace_id", None)
    if not ws_id:
        briefing, _ = await _resolve_briefing(surface, db)
        ws_id = getattr(briefing, "workspace_id", None) if briefing else None

    if not ws_id:
        return _empty_tab("events", "Could not resolve workspace for events.")

    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    result = await db.execute(
        select(NormalizedEvent)
        .where(
            NormalizedEvent.workspace_id == ws_id,
            NormalizedEvent.occurred_at >= cutoff,
        )
        .order_by(NormalizedEvent.occurred_at.desc())
        .limit(30)
    )
    events = list(result.scalars().all())

    if not events:
        return _empty_tab("events", "No events in the last 24 hours.")

    # Group by source
    by_source: dict[str, list] = {}
    for evt in events:
        by_source.setdefault(evt.source or "unknown", []).append(evt)

    sections: list[DetailSection] = []
    for source, source_events in by_source.items():
        children: list[A2UIComponent] = []
        for i, evt in enumerate(source_events[:10]):
            children.append(
                r.row(
                    f"{source}_{i}",
                    [
                        r.text(
                            f"{source}_{i}_title",
                            _truncate(evt.title or evt.event_type or "event", 80),
                        ),
                        r.caption(f"{source}_{i}_time", _format_ts(evt.occurred_at)),
                    ],
                )
            )
        sections.append(
            _section(f"src_{source}", f"{source} ({len(source_events)})", children, collapsed=False)
        )

    return DetailTabResponse(tab_id="events", sections=sections)


async def build_briefing_actions(
    db: AsyncSession, surface: Any, **kwargs: Any
) -> DetailTabResponse:
    briefing, had_id = await _resolve_briefing(surface, db)
    if not briefing:
        return _empty_tab(
            "actions",
            "Briefing not found." if had_id else _NO_BRIEFING_YET,
        )

    actions = briefing.recommended_actions or []
    children: list[A2UIComponent] = []
    for i, action in enumerate(actions):
        title = action.get("title", "") if isinstance(action, dict) else str(action)
        desc = action.get("description", "") if isinstance(action, dict) else ""
        children.append(r.text(f"act_{i}_title", title))
        if desc:
            children.append(r.markdown(f"act_{i}_desc", desc))
        if i < len(actions) - 1:
            children.append(r.divider(f"act_{i}_div"))

    if not children:
        return _empty_tab("actions", "No recommended actions.")
    return DetailTabResponse(
        tab_id="actions",
        sections=[_section("actions", "Recommended Actions", children, collapsed=False)],
    )
