"""Build a LangChain ``ChatAnthropic`` model from a Jarvis ``SubAgent``.

This is the Phase-1 model layer for the Deep Agents migration. It maps a Jarvis
agent's ``model_tier`` + ``thinking`` config onto the ``ChatAnthropic`` thinking/
effort/temperature surface, faithfully reproducing
``agent_loop.build_thinking_params`` (confirmed live in Phase 0).

Cheap mode is applied UPSTREAM by ``build_agent_set``/``apply_cheap_mode`` — this
factory only reads the (possibly already-downgraded) ``agent.model_tier`` and
``agent.thinking``; it does not re-implement cheap mode.
"""

from __future__ import annotations

from langchain_anthropic import ChatAnthropic

from src.deep_runtime._thinking import effort_for_budget, requires_adaptive_thinking
from src.orchestrator.agents import SubAgent

# Model-tier → direct Anthropic model id (CLAUDE.md / env).
MODEL_TIER_IDS: dict[str, str] = {
    "opus": "claude-opus-4-8",
    "sonnet": "claude-sonnet-4-6",
    "haiku": "claude-haiku-4-5-20251001",
}


def build_chat_model(agent: SubAgent) -> ChatAnthropic:
    """Construct a ``ChatAnthropic`` for *agent*, mirroring ``build_thinking_params``.

    - **Adaptive models** (Opus 4.7/4.8, Fable/Mythos 5): when thinking is enabled,
      pass ``thinking={"type":"adaptive","display":"summarized"}`` +
      ``effort=effort_for_budget(budget)`` and leave ``temperature`` unset (None is
      dropped from the request body). When disabled, omit both thinking and
      temperature — both 400 on these models.
    - **Legacy models** (Sonnet, Haiku, …): when thinking is enabled, pass
      ``thinking={"type":"enabled","budget_tokens":budget}`` + ``temperature=1``.
      When disabled, pass ``temperature=agent.temperature`` and no thinking.

    ``max_tokens`` is always forwarded from the agent.
    """
    model_id = MODEL_TIER_IDS[agent.model_tier]
    is_adaptive = requires_adaptive_thinking(model_id)
    thinking_cfg = agent.thinking

    kwargs: dict = {"model": model_id, "max_tokens": agent.max_tokens}

    if thinking_cfg.enabled:
        if is_adaptive:
            kwargs["thinking"] = {"type": "adaptive", "display": "summarized"}
            kwargs["effort"] = effort_for_budget(thinking_cfg.budget_tokens)
            # temperature left unset (omitted from request body).
        else:
            kwargs["thinking"] = {"type": "enabled", "budget_tokens": thinking_cfg.budget_tokens}
            kwargs["temperature"] = 1  # required when enabled-thinking is on
    else:
        if not is_adaptive:
            kwargs["temperature"] = agent.temperature
        # adaptive + no thinking → no temperature, no thinking (both 400).

    return ChatAnthropic(**kwargs)
