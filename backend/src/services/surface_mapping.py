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

from src.llm_utils import parse_llm_json

if TYPE_CHECKING:
    from src.contracts import PlanOutput, SurfaceDataPayload, SurfaceSpec
    from src.models.briefings import Briefing

logger = logging.getLogger(__name__)

_MD_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")  # [label](url) -> label (drop URL)
_MD_STRIP_RE = re.compile(r"(\*\*|__|~~|`|^#{1,6}\s*|^>\s*|^[-*+]\s+|-{3,})", re.MULTILINE)
_MD_EMPHASIS_RE = re.compile(r"(?<=\S)\*|\*(?=\S)")  # emphasis asterisks adjacent to text


def _plain_subtitle(text: str | None) -> str | None:
    """Reduce markdown-ish text to a plain one-line subtitle.

    Strips markdown links (keeping the link label), heading/emphasis/strong/
    strikethrough/code/rule/bullet syntax, and collapses whitespace so a surface
    subtitle is never a markdown blob. Returns the input unchanged when falsy
    (None stays None, "" stays "").

    Intentionally NOT exhaustive GFM: single-underscore emphasis is left alone
    (would corrupt snake_case), and setext headings / autolinks are out of scope.
    """
    if not text:
        return text
    cleaned = _MD_LINK_RE.sub(r"\1", text)
    cleaned = _MD_STRIP_RE.sub("", cleaned)
    cleaned = _MD_EMPHASIS_RE.sub("", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned or None


def derive_surface_kind(plan: "PlanOutput") -> tuple[str, str] | None:
    """Derive workspace surface kind from PlanOutput step capabilities.

    Returns (kind, default_title) or None if the plan is chat-only.
    """
    if not plan.steps:
        return None

    caps = {s.capability for s in plan.steps if s.actor == "muldro"}

    if caps <= {"reason", "respond", "none"}:
        return None

    if "system.add_to_brief" in caps:
        return ("briefing", "Briefing Update")
    if "system.schedule_reminder" in caps:
        return ("alert", "Reminder Scheduled")

    if any(s.risk in ("medium", "high") for s in plan.steps):
        return ("plan", "New Plan")

    muldro_steps = [s for s in plan.steps if s.actor == "muldro"]
    if len(muldro_steps) > 2:
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
    subtitle = _plain_subtitle(plan.reasoning)
    if subtitle:
        subtitle = subtitle[:120]
    metrics: list[SurfaceMetric] = []
    tags: list[str] = []

    if kind == "plan":
        step_count = len([s for s in plan.steps if s.actor == "muldro"])
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


def build_briefing_preview(briefing: "Briefing"):
    """Structured preview for a Briefing row — the single source of truth for a
    briefing card. The REST rebuild (SurfaceService._build_briefing_surface)
    calls this so a briefing card is structured, never a markdown blob.

    items = priority titles (top 5); metrics = Priorities/Actions counts;
    subtitle = first priority (plain text, capped).
    """
    from src.ui.contracts import SurfaceMetric, SurfacePreview

    priorities = briefing.top_priorities or []
    actions = briefing.recommended_actions or []

    def _priority_title(p) -> str:
        return (p.get("title", "") if isinstance(p, dict) else str(p)).strip()

    priority_titles = [t for t in (_priority_title(p) for p in priorities) if t]
    first_priority = priority_titles[0] if priority_titles else ""

    return SurfacePreview(
        title=briefing.headline or "Daily Briefing",
        subtitle=first_priority[:100] if first_priority else None,
        metrics=[
            SurfaceMetric(label="Priorities", value=str(len(priorities))),
            SurfaceMetric(label="Actions", value=str(len(actions))),
        ],
        items=priority_titles[:5],
        tags=["briefing"],
    )


# ── Surface cap ──────────────────────────────────────────────────

MAX_WORKSPACE_SURFACES = 20

PRIORITY_TIERS: dict[str, int] = {
    # Tier 0, alone. The prepared-work queue is the ONLY place a prepared action can be
    # acted on, so evicting its card does not merely hide information — it leaves a write
    # the system is blocked on with no discovery path at all. Every other kind is either
    # information about something that already happened (alert/briefing/summary), a
    # proposal (proactive_insight/recommendation), or reachable another way (a live
    # ``approval`` also renders inline inside its run surface). It costs one slot: the
    # queue is a SINGLETON per workspace, one card however long the queue is.
    #
    # It is NOT shared with ``approval``: tier 0 is not provably a singleton (persisted
    # approval surfaces still load from the DB), and sharing a tier with a kind that can
    # appear twenty times would reintroduce the eviction this ranking exists to prevent.
    "prepared_work": 0,
    "approval": 1,
    "plan": 2,
    "alert": 3,
    "briefing": 4,
    "proactive_insight": 5,
    "recommendation": 6,
    "summary": 7,
}

UNRANKED_TIER = max(PRIORITY_TIERS.values())
"""Tier for kinds absent from the table (e.g. ``run``) — they rank with the lowest
ranked kind. Derived from the table so inserting a tier cannot silently promote them."""


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
        key=lambda s: PRIORITY_TIERS.get(getattr(s, "kind", "summary"), UNRANKED_TIER),
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
    from pydantic import ValidationError

    from src.contracts import SurfaceSpec

    match = _SURFACE_SPEC_RE.search(response_text)
    if not match:
        return None

    try:
        data = parse_llm_json(match.group(1))
        if not isinstance(data, dict):
            logger.debug("SurfaceSpec block was not a JSON object; ignoring")
            return None
        return SurfaceSpec(**data)
    except (json.JSONDecodeError, ValidationError):
        # Malformed JSON or schema mismatch from the LLM → degrade to chat-only.
        # Unexpected exceptions are NOT swallowed here — let them surface.
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

    from src.contracts import SurfaceDataPayload

    match = _SURFACE_DATA_RE.search(response_text)
    if not match:
        return None

    try:
        raw = parse_llm_json(match.group(1))
    except json.JSONDecodeError:
        logger.debug("surface_data block is not valid JSON", exc_info=True)
        return None

    if not isinstance(raw, dict):
        logger.debug("surface_data block was not a JSON object; ignoring")
        return None

    try:
        return SurfaceDataPayload(**raw)
    except ValidationError:
        logger.warning("surface_data failed A2UI component validation", exc_info=True)
        return None
