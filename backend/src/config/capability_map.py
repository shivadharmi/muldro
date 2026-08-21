"""Translate neutral model inputs into provider-specific kwargs, keyed by
ModelSpec.thinking_style, so callers never 400 and never pay for reasoning they
asked not to have.

Two different things happen to a kwarg the neutral inputs do not want:

- **Dropped** when the model would *reject* it — temperature on a no-temperature
  model, a legacy thinking budget that cannot satisfy Anthropic's constraints.
- **Said explicitly** when omitting it would select a provider *default* rather
  than nothing. Reasoning models are the case that matters: leaving
  ``reasoning_effort`` off a gpt-5 call does not disable reasoning, it buys the
  default amount of it out of the caller's own token budget. See
  ``_OPENAI_NO_THINKING_EFFORT``.

Silence is not "off" — it is whatever the provider decides "off" means.
"""

from __future__ import annotations

from typing import Any

from src.config.model_catalog import ModelSpec

_EFFORT_LEVELS = {"none", "low", "medium", "high"}

# Anthropic's floor for legacy `thinking.budget_tokens`. The API also requires the
# budget to be strictly below max_tokens, so a completion sized at or under this
# floor cannot carry legacy thinking at all.
_MIN_LEGACY_THINKING_BUDGET = 1024

# How OpenAI says "do not reason". Omitting `reasoning_effort` does NOT disable
# reasoning on a gpt-5 model — it selects the provider's *default* effort, measured
# 2026-08-20 on `gpt-5-mini` with the real risk-assessor prompt at 192-384 reasoning
# tokens (median 256); `"minimal"` measured 0 in 6/6 on the same prompts, and both
# catalog OpenAI models (`gpt-5`, `gpt-5-mini`) return HTTP 200 for it. Reasoning
# tokens are drawn from the same max_completion_tokens budget as the visible answer,
# so an omitted kwarg let a 256-token utility completion be spent entirely on hidden
# reasoning and come back empty.
#
# This is deliberately NOT a member of _EFFORT_LEVELS: "minimal" is one provider's
# way of expressing the neutral level "none", not a fifth neutral level callers may
# choose. Every other style expresses "none" by omitting its own thinking kwarg.
_OPENAI_NO_THINKING_EFFORT = "minimal"


def build_model_kwargs(
    spec: ModelSpec,
    *,
    effort: str,
    max_tokens: int,
    temperature: float | None,
    thinking_enabled: bool,
) -> dict[str, Any]:
    """Return provider kwargs (excluding model id / api key) for *spec*."""
    kwargs: dict[str, Any] = {"max_tokens": max_tokens}
    effort = effort if effort in _EFFORT_LEVELS else "none"
    thinking_on = thinking_enabled and effort != "none"

    style = spec.thinking_style
    if style == "anthropic_adaptive":
        if thinking_on:
            kwargs["thinking"] = {"type": "adaptive", "display": "summarized"}
            kwargs["effort"] = effort
        # adaptive rejects temperature always
        return kwargs

    if style == "anthropic_legacy":
        # Legacy thinking is only representable when BOTH Anthropic constraints can
        # hold: budget_tokens >= _MIN_LEGACY_THINKING_BUDGET and budget_tokens <
        # max_tokens. That needs max_tokens > _MIN_LEGACY_THINKING_BUDGET; at or below
        # it, drop thinking rather than clamp to a budget the API rejects with a 400.
        if thinking_on and max_tokens > _MIN_LEGACY_THINKING_BUDGET:
            budget = _effort_to_budget(effort)
            if budget >= max_tokens:
                budget = max_tokens - 1
            kwargs["thinking"] = {"type": "enabled", "budget_tokens": budget}
            kwargs["temperature"] = 1  # required when legacy thinking on
        elif spec.accepts_temperature and temperature is not None:
            kwargs["temperature"] = temperature
        return kwargs

    if style == "openai_effort":
        if thinking_on:
            kwargs["reasoning_effort"] = effort
        else:
            kwargs["reasoning_effort"] = _OPENAI_NO_THINKING_EFFORT
        if spec.accepts_temperature and temperature is not None:
            kwargs["temperature"] = temperature
        return kwargs

    if style == "gemini":
        # Gemini 2.5 takes a token thinking budget (ChatGoogleGenerativeAI.thinking_budget);
        # map the neutral effort level so the Settings effort selector is not a no-op.
        if thinking_on:
            kwargs["thinking_budget"] = _effort_to_budget(effort)
        if spec.accepts_temperature and temperature is not None:
            kwargs["temperature"] = temperature
        return kwargs

    # "none": plain completion, no thinking shape.
    if spec.accepts_temperature and temperature is not None:
        kwargs["temperature"] = temperature
    return kwargs


def _effort_to_budget(effort: str) -> int:
    """Map a neutral effort level to a legacy-thinking token budget."""
    return {"low": 2048, "medium": 4096, "high": 8192}.get(effort, 2048)
