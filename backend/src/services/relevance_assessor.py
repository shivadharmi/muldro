"""LLM-based relevance assessment for perception signals.

Evaluates whether a user should care about a signal right now,
scoring relevance against their goals and routing to push/briefing/silent tiers.
"""

import json
import logging
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)

_HAIKU_MODEL_FALLBACK = "claude-haiku-4-5-20251001"


def _get_haiku_model() -> str:
    """Resolve Haiku model ID from settings, avoiding circular imports."""
    try:
        from src.config.settings import get_settings

        settings = get_settings()
        if settings.use_bedrock:
            from src.orchestrator.jarvis import BEDROCK_MODEL_TIERS

            return BEDROCK_MODEL_TIERS["haiku"]
        else:
            from src.orchestrator.jarvis import MODEL_TIERS

            return MODEL_TIERS["haiku"]
    except Exception:
        return _HAIKU_MODEL_FALLBACK


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
  "suggested_actions": []
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
    client: Any,
    model: str | None = None,
    engagement_context: str = "",
) -> RelevanceAssessment:
    """Call Haiku to assess signal relevance. Returns silent assessment on failure."""
    if model is None:
        model = _get_haiku_model()
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
        response = await client.messages.create(
            model=model,
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.content[0].text
        text = re.sub(r"^```\w*\n?", "", text.strip()).rstrip("`").strip()
        data = json.loads(text)
        assessment = RelevanceAssessment(**data)
        return assessment.model_copy(
            update={
                "notification_tier": _determine_tier(assessment.relevance_score, assessment.urgency)
            }
        )
    except Exception:
        logger.warning("Relevance assessment failed, defaulting to silent", exc_info=True)
        return RelevanceAssessment(
            relevance_score=0.0,
            reasoning="Assessment failed — defaulting to silent",
            notification_tier="silent",
        )
