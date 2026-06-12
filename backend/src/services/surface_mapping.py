"""Surface kind derivation, preview building, and spec extraction.

Functions for mapping PlanOutput capabilities to surface kinds, building
SurfacePreview data, extracting structured surface specs from Presenter
responses, and applying workspace surface caps.
"""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.orchestrator.contracts import PlanOutput, SurfaceDataPayload, SurfaceSpec

logger = logging.getLogger(__name__)


def derive_surface_kind(plan: "PlanOutput") -> tuple[str, str] | None:
    """Derive workspace surface kind from PlanOutput step capabilities.

    Returns (kind, default_title) or None if the plan is chat-only.
    """
    if not plan.steps:
        return None

    caps = {s.capability for s in plan.steps if s.actor == "jarvis"}

    if caps <= {"reason", "respond", "none"}:
        return None

    if "system.add_to_brief" in caps:
        return ("briefing", "Briefing Update")
    if "system.schedule_reminder" in caps:
        return ("alert", "Reminder Scheduled")

    if any(s.risk in ("medium", "high") for s in plan.steps):
        return ("plan", "New Plan")

    jarvis_steps = [s for s in plan.steps if s.actor == "jarvis"]
    if len(jarvis_steps) > 2:
        return ("plan", plan.goal[:80] or "Plan")

    return ("summary", "Summary")


def build_surface_preview_from_plan(
    plan: "PlanOutput",
    kind: str,
    default_title: str,
    response_text: str,
):
    """Build a SurfacePreview from a PlanOutput for workspace grid cards."""
    from src.ui.contracts import SurfaceMetric, SurfacePreview

    title = plan.goal[:80] if plan.goal else default_title
    subtitle = plan.reasoning[:120] if plan.reasoning else None
    metrics: list[SurfaceMetric] = []
    tags: list[str] = []

    if kind == "plan":
        step_count = len([s for s in plan.steps if s.actor == "jarvis"])
        if step_count:
            metrics.append(SurfaceMetric(label="Steps", value=str(step_count)))
        metrics.append(SurfaceMetric(label="Priority", value=plan.priority))
    elif kind == "summary":
        tags.append("read")
    elif kind == "briefing":
        tags.append("briefing")
    elif kind == "alert":
        tags.append("reminder")

    return SurfacePreview(
        title=title,
        subtitle=subtitle,
        status=None,
        priority=plan.priority if plan.priority != "medium" else None,
        metrics=metrics,
        entities=[],
        progress=None,
        tags=tags,
    )


# ── Surface cap ──────────────────────────────────────────────────

MAX_WORKSPACE_SURFACES = 20

PRIORITY_TIERS: dict[str, int] = {
    "approval": 0,
    "plan": 1,
    "alert": 2,
    "briefing": 3,
    "proactive_insight": 4,
    "recommendation": 5,
    "summary": 6,
    "checklist": 6,
    "comparison": 6,
    "timeline": 6,
    "table": 6,
    "activity": 6,
}


def apply_surface_cap(surfaces: list) -> list:
    """Apply priority-weighted cap to workspace surfaces.

    Sorts by (priority_tier, -created_at) and truncates to MAX_WORKSPACE_SURFACES.
    Higher-priority surfaces (lower tier number) survive eviction.
    Within the same tier, newer surfaces (later created_at) survive.

    Uses two-pass stable sort: first by created_at descending (newest first),
    then by tier ascending. Python's stable sort preserves the newest-first
    ordering within each tier.
    """
    if len(surfaces) <= MAX_WORKSPACE_SURFACES:
        return surfaces

    by_recency = sorted(
        surfaces,
        key=lambda s: getattr(s, "created_at", "") or "",
        reverse=True,
    )
    by_priority = sorted(
        by_recency,
        key=lambda s: PRIORITY_TIERS.get(getattr(s, "kind", "summary"), 6),
    )

    return by_priority[:MAX_WORKSPACE_SURFACES]


# ── Surface spec extraction ──────────────────────────────────────

_SURFACE_SPEC_RE = re.compile(r"```json:surface\s*\n(.*?)\n```", re.DOTALL)
_SURFACE_DATA_RE = re.compile(r"```json:surface_data\s*\n(.*?)\n```", re.DOTALL)
_ALL_SURFACE_BLOCKS_RE = re.compile(
    r"```json:(?:surface|surface_data)\s*\n.*?\n```\s*",
    re.DOTALL,
)
_COLLAPSE_BLANK_LINES_RE = re.compile(r"\n{3,}")


def strip_surface_blocks(text: str) -> str:
    """Remove ``` ```json:surface``` `` and ``` ```json:surface_data``` `` fenced blocks.

    Used to scrub the Presenter response before it is delivered to the user so the
    machine-readable surface specification / payload does not leak into chat.
    Collapses runs of three or more newlines left by the removed blocks.
    """
    if not text:
        return text
    stripped = _ALL_SURFACE_BLOCKS_RE.sub("", text)
    stripped = _COLLAPSE_BLANK_LINES_RE.sub("\n\n", stripped)
    return stripped.strip()


def extract_surface_spec(response_text: str) -> "SurfaceSpec | None":
    """Extract SurfaceSpec from ```json:surface``` block in Presenter response.

    Returns SurfaceSpec on success, None if not found or invalid.
    Best-effort — degrades to chat-only on failure.
    """
    from src.orchestrator.contracts import SurfaceSpec

    match = _SURFACE_SPEC_RE.search(response_text)
    if not match:
        return None

    try:
        data = json.loads(match.group(1))
        return SurfaceSpec(**data)
    except (json.JSONDecodeError, Exception):
        logger.debug("Failed to parse SurfaceSpec from response", exc_info=True)
        return None


def extract_surface_data(response_text: str) -> "SurfaceDataPayload | None":
    """Extract and validate structured surface content from ``` ```json:surface_data``` ``.

    Returns a typed :class:`SurfaceDataPayload` whose ``sections`` are full
    :class:`A2UIComponent` trees. Returns ``None`` if the block is missing,
    not valid JSON, or if any section fails A2UI component validation (invalid
    ``type``, missing ``id``, or properties that don't match the registered
    Pydantic property model for that type).
    """
    from pydantic import ValidationError

    from src.orchestrator.contracts import SurfaceDataPayload

    match = _SURFACE_DATA_RE.search(response_text)
    if not match:
        return None

    try:
        raw = json.loads(match.group(1))
    except json.JSONDecodeError:
        logger.debug("surface_data block is not valid JSON", exc_info=True)
        return None

    try:
        return SurfaceDataPayload(**raw)
    except ValidationError:
        logger.warning("surface_data failed A2UI component validation", exc_info=True)
        return None
