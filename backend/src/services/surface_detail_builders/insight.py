"""Proactive insight surface detail tab builders."""

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from src.ui import renderer as r
from src.ui.contracts import A2UIComponent, DetailTabResponse

from ._shared import (
    _empty_tab,
    _get_payload,
    _section,
)

logger = logging.getLogger(__name__)


async def build_insight_signal(db: AsyncSession, surface: Any, **kwargs: Any) -> DetailTabResponse:
    """Insight signal — source, summary, relevance score and reasoning."""
    payload = _get_payload(surface)
    insight_data = payload.get("insight_data", {})

    if not insight_data:
        return _empty_tab("signal", "No insight data available.")

    children: list[A2UIComponent] = []
    signal_source = insight_data.get("signal_source", "")
    if signal_source:
        children.append(r.badge("ins_source", signal_source))

    signal_summary = insight_data.get("signal_summary", "")
    if signal_summary:
        children.append(r.text("ins_summary", signal_summary))

    relevance_score = insight_data.get("relevance_score")
    if relevance_score is not None:
        children.append(r.metric("ins_relevance", "Relevance", relevance_score))

    relevance_reasoning = insight_data.get("relevance_reasoning", "")
    if relevance_reasoning:
        children.append(r.caption("ins_reasoning", relevance_reasoning))

    if not children:
        return _empty_tab("signal", "No signal details available.")

    return DetailTabResponse(
        tab_id="signal",
        sections=[_section("signal", "Signal Details", children, collapsed=False)],
    )


async def build_insight_actions(db: AsyncSession, surface: Any, **kwargs: Any) -> DetailTabResponse:
    """Insight actions — suggested actions with descriptions and execute buttons."""
    payload = _get_payload(surface)
    insight_data = payload.get("insight_data", {})
    actions = insight_data.get("suggested_actions", [])

    if not actions:
        return _empty_tab("actions", "No suggested actions.")

    children: list[A2UIComponent] = []
    for i, action in enumerate(actions):
        if not isinstance(action, dict):
            continue
        desc = action.get("description", "")
        capability = action.get("capability", "")
        card_children: list[A2UIComponent] = []
        if desc:
            card_children.append(r.text(f"act_{i}_desc", desc))
        if capability:
            card_children.append(r.badge(f"act_{i}_cap", capability))
        card_children.append(
            r.button(
                f"act_{i}_exec",
                "Execute",
                variant="primary",
                action_payload={
                    "action": "execute_insight_action",
                    "index": i,
                    "capability": capability,
                },
            )
        )
        children.append(r.card(f"act_{i}", card_children))

    if not children:
        return _empty_tab("actions", "No suggested actions.")

    title = f"Suggested Actions ({len(children)})"
    return DetailTabResponse(
        tab_id="actions",
        sections=[_section("actions", title, children, collapsed=False)],
    )


async def build_insight_context(db: AsyncSession, surface: Any, **kwargs: Any) -> DetailTabResponse:
    """Insight context — related goals from insight data."""
    payload = _get_payload(surface)
    insight_data = payload.get("insight_data", {})
    goals = insight_data.get("related_goals", [])

    if not goals:
        return _empty_tab("context", "No related goals.")

    children: list[A2UIComponent] = [r.text(f"goal_{i}", str(goal)) for i, goal in enumerate(goals)]
    return DetailTabResponse(
        tab_id="context",
        sections=[_section("goals", "Related Goals", children, collapsed=False)],
    )
