"""Unit tests for deep_runtime.model_factory.build_chat_model.

No live API calls — these only inspect the constructed ChatAnthropic object's
attributes (.model, .thinking, .effort, .temperature, .max_tokens) to prove the
thinking-params branching is ported faithfully from agent_loop.build_thinking_params.

Confirmed (against langchain-anthropic 1.4.6) attribute storage:
  - .model       -> the model id string (there is NO .model_name attribute)
  - .thinking    -> dict or None
  - .effort      -> str or None
  - .temperature -> float or None (None is dropped from the request body at send)
  - .max_tokens  -> int
"""

from __future__ import annotations

from src.deep_runtime.model_factory import build_chat_model
from src.orchestrator.agents import SubAgent, ThinkingConfig


def _agent(
    *,
    model_tier: str,
    thinking_enabled: bool,
    budget_tokens: int,
    temperature: float = 0.3,
    max_tokens: int = 4096,
) -> SubAgent:
    return SubAgent(
        name="t",
        prompt="role prompt",
        model_tier=model_tier,
        capability_scope=set(),
        max_tokens=max_tokens,
        temperature=temperature,
        thinking=ThinkingConfig(enabled=thinking_enabled, budget_tokens=budget_tokens),
    )


def test_planner_opus_adaptive_thinking_high_effort_temp_unset():
    """Opus (adaptive model) + thinking budget 8192 → adaptive thinking dict,
    effort=high, temperature unset (None), model claude-opus-4-8."""
    agent = _agent(model_tier="opus", thinking_enabled=True, budget_tokens=8192, max_tokens=8192)
    model = build_chat_model(agent)

    assert model.model == "claude-opus-4-8"
    assert model.thinking == {"type": "adaptive", "display": "summarized"}
    assert model.effort == "high"
    assert model.temperature is None
    assert model.max_tokens == 8192


def test_sonnet_legacy_enabled_thinking_temperature_one():
    """Sonnet (legacy model) + thinking budget 4096 with max_tokens 4096 → legacy
    enabled-thinking dict, temperature == 1, model claude-sonnet-4-6, no effort. The budget
    is CLAMPED to max_tokens - 1 (4095): Anthropic requires max_tokens > budget_tokens
    (thinking counts toward max_tokens), so the raw-equal 4096/4096 would 400 — the clamp
    mirrors legacy build_thinking_params (agent_loop.py:458-459)."""
    agent = _agent(model_tier="sonnet", thinking_enabled=True, budget_tokens=4096)
    model = build_chat_model(agent)

    assert model.model == "claude-sonnet-4-6"
    assert model.thinking == {"type": "enabled", "budget_tokens": 4095}
    assert model.temperature == 1
    assert model.effort is None
    assert model.max_tokens == 4096


def test_haiku_model_id():
    """Haiku tier maps to claude-haiku-4-5-20251001."""
    agent = _agent(model_tier="haiku", thinking_enabled=True, budget_tokens=2048)
    model = build_chat_model(agent)

    assert model.model == "claude-haiku-4-5-20251001"


def test_adaptive_effort_tiers_from_budget():
    """_effort_for_budget mapping on adaptive models: <4096→low, 4096..8191→medium,
    None or >=8192→high."""
    low = build_chat_model(_agent(model_tier="opus", thinking_enabled=True, budget_tokens=2048))
    medium = build_chat_model(_agent(model_tier="opus", thinking_enabled=True, budget_tokens=4096))
    high = build_chat_model(_agent(model_tier="opus", thinking_enabled=True, budget_tokens=8192))

    assert low.effort == "low"
    assert medium.effort == "medium"
    assert high.effort == "high"


def test_thinking_disabled_adaptive_omits_thinking_and_temperature():
    """Thinking disabled on an adaptive model: no thinking, no temperature (both 400)."""
    agent = _agent(model_tier="opus", thinking_enabled=False, budget_tokens=8192, temperature=0.3)
    model = build_chat_model(agent)

    assert model.model == "claude-opus-4-8"
    assert model.thinking is None
    assert model.effort is None
    assert model.temperature is None


def test_thinking_disabled_legacy_uses_agent_temperature():
    """Thinking disabled on a legacy model: no thinking, temperature == agent.temperature."""
    agent = _agent(model_tier="sonnet", thinking_enabled=False, budget_tokens=4096, temperature=0.3)
    model = build_chat_model(agent)

    assert model.model == "claude-sonnet-4-6"
    assert model.thinking is None
    assert model.temperature == 0.3


def test_max_tokens_always_passed():
    """max_tokens is always forwarded from the agent."""
    agent = _agent(model_tier="haiku", thinking_enabled=False, budget_tokens=2048, max_tokens=1234)
    model = build_chat_model(agent)
    assert model.max_tokens == 1234
