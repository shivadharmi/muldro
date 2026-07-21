"""UtilityLLM — the single seam for plain (non-streaming, non-tool) LLM completions.

The 12 shared-machinery consumers (risk_assessor, relevance_assessor, event_processor,
world_model, memory extraction, verifier, presenter, context summarize, intent_classifier,
governor critique) call ``complete_text`` instead of the raw Anthropic client. Each keeps its
OWN ``llm_utils.parse_llm_json`` + domain fallback — this seam only fetches text.
"""

from __future__ import annotations

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from src.llm.model_factory import build_utility_model


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
    model = build_utility_model(tier, max_tokens=max_tokens, temperature=temperature)
    messages: list[BaseMessage] = []
    if system is not None:
        messages.append(SystemMessage(content=system))
    messages.append(HumanMessage(content=user))
    response = await model.ainvoke(messages)
    content = response.content
    if isinstance(content, str):
        return content
    # Defensive: a block list (utility calls have no thinking, so this is rare) — join text blocks.
    return "".join(
        b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"
    )
