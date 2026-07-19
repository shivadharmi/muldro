"""UtilityLLM — the single seam for plain (non-streaming, non-tool) LLM completions.

The 12 shared-machinery consumers (risk_assessor, relevance_assessor, event_processor,
world_model, memory extraction, verifier, presenter, context summarize, intent_classifier,
governor critique) call ``complete_text`` instead of the raw Anthropic client. Each keeps its
OWN ``llm_utils.parse_llm_json`` + domain fallback — this seam only fetches text.
"""

from __future__ import annotations

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from src.llm.model_factory import build_utility_model


async def complete_text(
    *,
    system: str | list | None,
    user: str,
    tier: str,
    max_tokens: int,
    temperature: float | None = None,
    prefill: str | None = None,
) -> str:
    """Run one plain completion and return the assistant's text.

    - ``system``: plain string, a list of content blocks, or ``None`` (omitted).
    - ``prefill``: optional assistant partial (e.g. ``"{"``); the returned text is the
      CONTINUATION (does not include the prefill) — callers re-prepend if needed,
      matching the raw-SDK prefill behavior.
    """
    model = build_utility_model(tier, max_tokens=max_tokens, temperature=temperature)
    messages: list[BaseMessage] = []
    if system is not None:
        messages.append(SystemMessage(content=system))
    messages.append(HumanMessage(content=user))
    if prefill is not None:
        messages.append(AIMessage(content=prefill))
    response = await model.ainvoke(messages)
    content = response.content
    if isinstance(content, str):
        return content
    # Defensive: a block list (utility calls have no thinking, so this is rare) — join text blocks.
    return "".join(
        b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"
    )
