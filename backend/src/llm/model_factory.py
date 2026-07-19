"""Provider-simple LangChain model construction (pure Claude API, no Bedrock).

The SINGLE place that builds a ``ChatAnthropic``. Both the deep-agent path
(``deep_runtime.model_factory.build_chat_model``) and utility completions
(``build_utility_model`` -> ``src.llm.utility.complete_text``) funnel through
``build_langchain_model`` so the api-key + param surface lives in one leaf,
importable downward by both services and the deep runtime.
"""

from __future__ import annotations

from langchain_anthropic import ChatAnthropic

from src.config.models import MODEL_TIERS
from src.config.settings import get_settings


def build_langchain_model(
    model_id: str,
    *,
    max_tokens: int,
    temperature: float | None = None,
    thinking: dict | None = None,
    effort: str | None = None,
) -> ChatAnthropic:
    """Construct a direct-Anthropic ``ChatAnthropic``.

    Only non-None optional params are forwarded (a None temperature/thinking/effort
    is omitted from the request body). The api key is passed explicitly because
    LangChain otherwise reads the unprefixed ``ANTHROPIC_API_KEY`` which Jarvis never
    sets (it uses ``JARVIS_ANTHROPIC_API_KEY`` -> ``settings.anthropic_api_key``).
    """
    kwargs: dict = {"model": model_id, "max_tokens": max_tokens}
    api_key = get_settings().anthropic_api_key
    if api_key:
        kwargs["api_key"] = api_key
    if thinking is not None:
        kwargs["thinking"] = thinking
    if effort is not None:
        kwargs["effort"] = effort
    if temperature is not None:
        kwargs["temperature"] = temperature
    return ChatAnthropic(**kwargs)


def _resolve_utility_model_id(tier: str) -> str:
    """Map a utility tier to a DIRECT Anthropic model id (no Bedrock).

    - ``"haiku"`` -> the Haiku tier id.
    - anything else (``"resolved"``/``"sonnet"``) -> the configured direct model
      (``settings.anthropic_model``), preserving the ``JARVIS_ANTHROPIC_MODEL`` override
      that the raw-SDK consumers honored via ``resolved_model``.
    """
    if tier == "haiku":
        return MODEL_TIERS["haiku"]
    return get_settings().anthropic_model


def build_utility_model(
    tier: str, *, max_tokens: int, temperature: float | None = None
) -> ChatAnthropic:
    """Build a thinking-free ``ChatAnthropic`` for a plain utility completion."""
    return build_langchain_model(
        _resolve_utility_model_id(tier), max_tokens=max_tokens, temperature=temperature
    )
