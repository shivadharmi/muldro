"""UtilityLLM — the single seam for plain (non-streaming, non-tool) LLM completions.

The 12 shared-machinery consumers (risk_assessor, relevance_assessor, event_processor,
world_model, memory extraction, verifier, presenter, context summarize, intent_classifier,
governor critique) call ``complete_text`` instead of the raw Anthropic client. Each keeps its
OWN ``llm_utils.parse_llm_json`` + domain fallback — this seam only fetches text.
"""

from __future__ import annotations

from dataclasses import dataclass

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from src.llm.model_factory import build_utility_model


@dataclass(frozen=True)
class LLMUsage:
    """Normalized per-call token usage for a utility completion.

    Mirrors the fields ``BudgetTracker.record_usage`` needs, so callers that want to
    instrument a direct ``complete_text`` call (which bypasses the deep-runtime budget
    middleware) can record a span without re-deriving the shape. All-zero when the
    model returns no ``usage_metadata``.
    """

    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0


def _extract_text(content) -> str:
    """Return the assistant text from a str or a content-block list."""
    if isinstance(content, str):
        return content
    # Defensive: a block list (utility calls have no thinking, so this is rare) — join text blocks.
    return "".join(
        b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"
    )


def _usage_from_response(response, model_id: str) -> LLMUsage:
    """Build an ``LLMUsage`` from a LangChain response's ``usage_metadata`` (or zeros)."""
    usage = getattr(response, "usage_metadata", None) or {}
    details = usage.get("input_token_details") or {}
    return LLMUsage(
        model=model_id,
        input_tokens=usage.get("input_tokens", 0) or 0,
        output_tokens=usage.get("output_tokens", 0) or 0,
        cache_creation_input_tokens=details.get("cache_creation", 0) or 0,
        cache_read_input_tokens=details.get("cache_read", 0) or 0,
    )


async def complete_text_with_usage(
    *,
    system: str | list | None,
    user: str,
    tier: str,
    max_tokens: int,
    temperature: float | None = None,
) -> tuple[str, LLMUsage]:
    """Run one plain completion and return ``(text, usage)``.

    Same contract as :func:`complete_text` (see its docstring for the no-prefill rule),
    but also surfaces the call's token usage so perception-path callers can record a
    ``TokenUsage`` span — these direct calls otherwise bypass the deep-runtime budget
    middleware and are invisible in cost accounting.
    """
    model = build_utility_model(tier, max_tokens=max_tokens, temperature=temperature)
    messages: list[BaseMessage] = []
    if system is not None:
        messages.append(SystemMessage(content=system))
    messages.append(HumanMessage(content=user))
    response = await model.ainvoke(messages)
    model_id = getattr(model, "model", tier)
    return _extract_text(response.content), _usage_from_response(response, model_id)


async def complete_text(
    *,
    system: str | list | None,
    user: str,
    tier: str,
    max_tokens: int,
    temperature: float | None = None,
) -> str:
    """Run one plain completion and return the assistant's text.

    - ``system``: plain string, a list of content blocks, or ``None`` (omitted).

    The conversation always ends with the user message. Assistant-message *prefill*
    (seeding the reply with ``"{"`` to force JSON) is intentionally NOT supported:
    every model Jarvis runs is an adaptive-thinking model, and those reject a
    conversation that ends with an assistant turn (400 "does not support assistant
    message prefill"). Callers that need JSON instruct it in the system prompt and
    lean on ``llm_utils.parse_llm_json``, which tolerates fences and surrounding prose.
    """
    text, _ = await complete_text_with_usage(
        system=system, user=user, tier=tier, max_tokens=max_tokens, temperature=temperature
    )
    return text
