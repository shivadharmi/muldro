"""LLM-based relevance assessment for perception signals.

Evaluates whether a user should care about a signal right now,
scoring relevance against their goals and routing to push/briefing/silent tiers.
"""

import logging
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.llm.utility import complete_text
from src.llm_utils import parse_llm_json

logger = logging.getLogger(__name__)


class SuggestedAction(BaseModel):
    """An action the system could take in response to a signal."""

    model_config = ConfigDict(extra="ignore")
    description: str
    capability: str
    action_input: dict[str, Any] = Field(default_factory=dict)


class RelevanceAssessment(BaseModel):
    """Result of LLM relevance assessment for a perception signal."""

    model_config = ConfigDict(extra="ignore")
    relevance_score: float = 0.0
    reasoning: str = ""
    relates_to_goals: list[str] = Field(default_factory=list)
    urgency: Literal["immediate", "today", "this_week", "whenever"] = "whenever"
    suggested_actions: list[SuggestedAction] = Field(default_factory=list)
    notification_tier: Literal["push", "briefing", "silent"] = "silent"
    # Number of supporting observations behind this signal (e.g. recurrences,
    # days observed). Surfaced as human-readable evidence on the insight card.
    evidence_count: int | None = None
    # Unit for evidence_count, used to phrase the evidence string (e.g.
    # "recurrences", "days observed"). Defaults to a generic noun.
    evidence_unit: str | None = None

    @field_validator("suggested_actions", mode="before")
    @classmethod
    def _coerce_string_actions(cls, v: Any) -> list[dict[str, Any]]:
        """Coerce plain strings into SuggestedAction dicts."""
        if not isinstance(v, list):
            return []
        return [
            {"description": item, "capability": "system.respond"} if isinstance(item, str) else item
            for item in v
        ]


class PerceptionSignal(BaseModel):
    """A normalized perception signal to be assessed."""

    model_config = ConfigDict(extra="ignore")
    source: str
    event_type: str
    summary: str
    entities: list[str] = Field(default_factory=list)
    raw_data: dict[str, Any] = Field(default_factory=dict)


class UserContext(BaseModel):
    """User context for relevance assessment."""

    model_config = ConfigDict(extra="ignore")
    goals: list[str] = Field(default_factory=list)
    recent_activity: str = ""
    preferences: list[str] = Field(default_factory=list)


def format_evidence(count: int | None, unit: str | None) -> str | None:
    """Format a supporting-observation count into a human-readable evidence string.

    Returns ``None`` when there is no usable count (so the insight card simply
    omits the evidence line). Examples::

        format_evidence(4, "recurrences")   -> "4 recurrences"
        format_evidence(42, "days observed") -> "42 days observed"
        format_evidence(3, None)             -> "3 observed"
    """
    if count is None or count <= 0:
        return None
    label = (unit or "").strip() or "observed"
    return f"{count} {label}"


def _determine_tier(
    relevance_score: float,
    urgency: Literal["immediate", "today", "this_week", "whenever"],
) -> Literal["push", "briefing", "silent"]:
    """Pure function: map relevance score + urgency to notification tier.

    Routing logic:
        relevance >= 0.7 AND urgency in (immediate, today, this_week) → push
        relevance >= 0.4                                                → briefing
        relevance < 0.4                                                 → silent
    """
    if relevance_score >= 0.7 and urgency in ("immediate", "today", "this_week"):
        return "push"
    if relevance_score >= 0.4:
        return "briefing"
    return "silent"


_RELEVANCE_PROMPT = """\
You are a relevance assessor for a personal AI assistant. Given a signal from \
a data source and the user's current context, assess whether the user should \
care about this right now.

Respond with a JSON object (no markdown fences):
{{
  "relevance_score": <float 0.0-1.0>,
  "reasoning": "<why this matters or doesn't>",
  "relates_to_goals": ["<goal text if relevant>"],
  "urgency": "<immediate|today|this_week|whenever>",
  "suggested_actions": [{{"description": "<what to do>", "capability": "<capability.name>"}}],
  "evidence_count": <integer count of supporting observations, or null>,
  "evidence_unit": "<unit for evidence_count, e.g. recurrences or days observed, or null>"
}}

User goals: {goals}
Recent activity: {recent_activity}
User preferences: {preferences}

Signal source: {source}
Event type: {event_type}
Summary: {summary}
"""


async def assess_relevance(
    signal: PerceptionSignal,
    user_context: UserContext,
    engagement_context: str = "",
    relevance_penalty: float = 0.0,
    workspace_id: str = "",
) -> RelevanceAssessment:
    """Call Haiku to assess signal relevance. Returns silent assessment on failure.

    ``relevance_penalty`` (0.0-1.0) is a deterministic downgrade derived from the
    user's dismissal history (see EngagementService.get_relevance_penalty). It is
    subtracted from the LLM's score before the tier is determined, so repeatedly
    dismissed signal types are demoted even when the LLM rates them highly.
    """
    try:
        prompt = _RELEVANCE_PROMPT.format(
            goals=", ".join(user_context.goals) or "none specified",
            recent_activity=user_context.recent_activity or "none",
            preferences=", ".join(user_context.preferences) or "none",
            source=signal.source,
            event_type=signal.event_type,
            summary=signal.summary,
        )
        if engagement_context:
            prompt += f"\n\nEngagement history:\n{engagement_context}"
        text = await complete_text(
            system=None,
            user=prompt,
            tier="haiku",
            max_tokens=512,
            workspace_id=workspace_id,
        )
        data = parse_llm_json(text)
        assessment = RelevanceAssessment.model_validate(data)
        adjusted_score = max(0.0, assessment.relevance_score - relevance_penalty)
        return assessment.model_copy(
            update={
                "relevance_score": adjusted_score,
                "notification_tier": _determine_tier(adjusted_score, assessment.urgency),
            }
        )
    except Exception:
        logger.warning("Relevance assessment failed, defaulting to silent", exc_info=True)
        return RelevanceAssessment(
            relevance_score=0.0,
            reasoning="Assessment failed — defaulting to silent",
            notification_tier="silent",
        )
