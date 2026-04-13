"""Surface kind derivation and preview building for WS surface pushes.

These functions map PlanOutput capabilities to surface kinds and build
SurfacePreview data for workspace grid cards. Phase 1 relocates them
from jarvis.py; Phase 3 replaces them with Presenter-driven SurfaceSpec.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.orchestrator.contracts import PlanOutput


def derive_surface_kind(plan: "PlanOutput") -> tuple[str, str] | None:
    """Derive workspace surface kind from PlanOutput step capabilities.

    Returns (kind, default_title) or None if the plan is chat-only.
    """
    if not plan.steps:
        return None

    caps = {s.capability for s in plan.steps if s.actor == "jarvis"}

    # Respond/reason only -> no surface (chat-only)
    # "none" = planner indicated no external capability needed (pure reasoning)
    if caps <= {"reason", "respond", "none"}:
        return None

    # System capabilities with visual value
    if "system.add_to_brief" in caps:
        return ("briefing", "Briefing Update")
    if "system.schedule_reminder" in caps:
        return ("alert", "Reminder Scheduled")

    # Write actions -> plan surface
    if any(s.risk in ("medium", "high") for s in plan.steps):
        return ("plan", "New Plan")

    # Multi-step -> plan surface
    jarvis_steps = [s for s in plan.steps if s.actor == "jarvis"]
    if len(jarvis_steps) > 2:
        return ("plan", plan.goal[:80] or "Plan")

    # Single/dual read -> summary
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

    # Pass 1: sort by created_at descending (newest first)
    by_recency = sorted(
        surfaces,
        key=lambda s: getattr(s, "created_at", "") or "",
        reverse=True,
    )
    # Pass 2: stable sort by tier ascending (highest priority first)
    by_priority = sorted(
        by_recency,
        key=lambda s: PRIORITY_TIERS.get(getattr(s, "kind", "summary"), 6),
    )

    return by_priority[:MAX_WORKSPACE_SURFACES]
