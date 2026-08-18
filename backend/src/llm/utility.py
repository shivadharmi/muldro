"""UtilityLLM — the single seam for plain (non-streaming, non-tool) LLM completions.

The 12 shared-machinery consumers (risk_assessor, relevance_assessor, event_processor,
world_model, memory extraction, verifier, presenter, context summarize, intent_classifier,
governor critique) call ``complete_text`` instead of the raw Anthropic client. Each keeps its
OWN ``llm_utils.parse_llm_json`` + domain fallback — this seam only fetches text.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from src.config.model_catalog import get_model_spec
from src.llm.model_factory import build_langchain_model
from src.models.database import get_session_factory
from src.services.model_resolver import ModelResolver, ResolvedModel

# Legacy utility-tier names -> ModelResolver tiers. Callers still pass the old
# names ("haiku"/"resolved"/"sonnet"); the resolver speaks fast/balanced/reasoning.
_UTILITY_TIER_MAP = {"haiku": "fast", "resolved": "balanced", "sonnet": "balanced"}


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


def _with_utility_params(
    resolved: ResolvedModel, *, max_tokens: int, temperature: float | None
) -> ResolvedModel:
    """Return a new ResolvedModel with utility per-call overrides applied.

    ``max_tokens`` always wins (each caller sizes its own completion). ``temperature``
    is applied only when the model's catalog spec accepts it — adaptive-thinking models
    reject a temperature and would 400.
    """
    kwargs = dict(resolved.kwargs)
    kwargs["max_tokens"] = max_tokens
    if temperature is not None:
        spec = get_model_spec(resolved.provider, resolved.model_id)
        if spec and spec.accepts_temperature:
            kwargs["temperature"] = temperature
    return replace(resolved, kwargs=kwargs)


async def complete_text_with_usage(
    *,
    system: str | list | None,
    user: str,
    tier: str,
    max_tokens: int,
    temperature: float | None = None,
    workspace_id: str | None = None,
) -> tuple[str, LLMUsage]:
    """Run one plain completion and return ``(text, usage)``.

    Same contract as :func:`complete_text` (see its docstring for the no-prefill rule),
    but also surfaces the call's token usage so perception-path callers can record a
    ``TokenUsage`` span — these direct calls otherwise bypass the deep-runtime budget
    middleware and are invisible in cost accounting.

    The legacy ``tier`` ("haiku"/"resolved"/"sonnet") is mapped onto a ModelResolver
    tier and resolved inside a short-lived DB session; utility completions never enable
    thinking.
    """
    mapped_tier = _UTILITY_TIER_MAP.get(tier, tier)
    # Several perception callers default workspace_id to "" rather than None. An empty
    # string matches no ModelBinding row, so it would silently degrade to the deployment
    # default instead of erroring — normalize it to None so "no workspace" is stated once.
    async with get_session_factory()() as db:
        resolved = await ModelResolver(db).resolve(
            tier=mapped_tier, workspace_id=workspace_id or None, thinking_enabled=False
        )
    resolved = _with_utility_params(resolved, max_tokens=max_tokens, temperature=temperature)
    model = build_langchain_model(resolved)
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
    workspace_id: str | None = None,
) -> str:
    """Run one plain completion and return the assistant's text.

    - ``system``: plain string, a list of content blocks, or ``None`` (omitted).
    - ``workspace_id``: resolve against this workspace's model bindings. Omitting it
      resolves against the deployment default, so a caller with a workspace in scope
      MUST pass it — otherwise the workspace's configured model is silently ignored
      for this call while its tokens are still billed to that workspace.

    The conversation always ends with the user message. Assistant-message *prefill*
    (seeding the reply with ``"{"`` to force JSON) is intentionally NOT supported:
    every model Muldro runs is an adaptive-thinking model, and those reject a
    conversation that ends with an assistant turn (400 "does not support assistant
    message prefill"). Callers that need JSON instruct it in the system prompt and
    lean on ``llm_utils.parse_llm_json``, which tolerates fences and surrounding prose.
    """
    text, _ = await complete_text_with_usage(
        system=system,
        user=user,
        tier=tier,
        max_tokens=max_tokens,
        temperature=temperature,
        workspace_id=workspace_id,
    )
    return text
