"""Tests for cheap-mode cost preset and budget defaults.

Cheap mode trades a little quality for ~65% lower cost by removing the Opus
tier (opus→sonnet) and halving per-agent thinking budgets.
"""

from src.config.settings import Settings
from src.orchestrator.agents import (
    AGENTS,
    ThinkingConfig,
    apply_cheap_mode,
    build_agent_set,
)


def test_default_daily_budget_is_25():
    # Raised from $5 — the old default silently degraded after ~2-3 messages.
    assert Settings().daily_token_budget_usd == 25.0


def test_cheap_mode_defaults_off():
    assert Settings().cheap_mode is False


def test_apply_cheap_mode_downgrades_opus_to_sonnet():
    planner = AGENTS["planner"]
    assert planner.model_tier == "opus"  # precondition
    cheap = apply_cheap_mode(planner)
    assert cheap.model_tier == "sonnet"


def test_apply_cheap_mode_leaves_sonnet_and_haiku_unchanged():
    assert apply_cheap_mode(AGENTS["perceiver"]).model_tier == "sonnet"
    assert apply_cheap_mode(AGENTS["persona"]).model_tier == "haiku"


def test_apply_cheap_mode_halves_thinking_budget_with_floor():
    planner = AGENTS["planner"]  # thinking budget 8192
    cheap = apply_cheap_mode(planner)
    assert cheap.thinking.budget_tokens == 4096


def test_apply_cheap_mode_thinking_budget_never_below_floor():
    from dataclasses import replace

    tiny = replace(AGENTS["persona"], thinking=ThinkingConfig(enabled=True, budget_tokens=1500))
    assert apply_cheap_mode(tiny).thinking.budget_tokens == 1024


def test_apply_cheap_mode_does_not_mutate_original():
    planner = AGENTS["planner"]
    original_tier = planner.model_tier
    original_budget = planner.thinking.budget_tokens
    apply_cheap_mode(planner)
    assert planner.model_tier == original_tier
    assert planner.thinking.budget_tokens == original_budget


def test_build_agent_set_noop_when_disabled():
    built = build_agent_set(AGENTS, cheap_mode=False)
    assert built["planner"].model_tier == "opus"
    assert built is not AGENTS  # a copy, not the shared singleton


def test_build_agent_set_applies_cheap_mode_to_all():
    built = build_agent_set(AGENTS, cheap_mode=True)
    assert built["planner"].model_tier == "sonnet"
    assert all(a.model_tier != "opus" for a in built.values())
