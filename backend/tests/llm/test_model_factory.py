"""Unit tests for src.llm.model_factory — no live API calls; inspect ChatAnthropic attrs.

langchain-anthropic 1.4.6 attribute storage (see tests/deep_runtime/test_model_factory.py):
  .model -> id string (no .model_name); .thinking -> dict|None; .effort -> str|None;
  .temperature -> float|None (None dropped from request body); .max_tokens -> int.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from src.llm.model_factory import build_langchain_model, build_utility_model


def test_plain_model_only_model_and_max_tokens():
    m = build_langchain_model("claude-haiku-4-5-20251001", max_tokens=256)
    assert m.model == "claude-haiku-4-5-20251001"
    assert m.max_tokens == 256
    assert m.thinking is None
    assert m.effort is None
    assert m.temperature is None  # unset -> omitted from request body


def test_temperature_forwarded_when_set():
    m = build_langchain_model("claude-sonnet-4-6", max_tokens=512, temperature=0.0)
    assert m.temperature == 0.0


def test_thinking_and_effort_forwarded():
    m = build_langchain_model(
        "claude-opus-4-8",
        max_tokens=8192,
        thinking={"type": "adaptive", "display": "summarized"},
        effort="high",
    )
    assert m.thinking == {"type": "adaptive", "display": "summarized"}
    assert m.effort == "high"


def test_utility_haiku_tier_direct_id():
    m = build_utility_model("haiku", max_tokens=256)
    assert m.model == "claude-haiku-4-5-20251001"
    assert m.max_tokens == 256
    assert m.thinking is None  # utility calls never think


def test_utility_resolved_tier_uses_settings_anthropic_model():
    # "resolved" tier honors the configured direct model (JARVIS_ANTHROPIC_MODEL override).
    fake = MagicMock()
    fake.anthropic_model = "claude-sonnet-4-6"
    fake.anthropic_api_key = "test-key"
    with patch("src.llm.model_factory.get_settings", return_value=fake):
        m = build_utility_model("resolved", max_tokens=512, temperature=0.0)
    assert m.model == "claude-sonnet-4-6"
    assert m.temperature == 0.0


def test_unknown_utility_tier_raises():
    import pytest

    with pytest.raises(ValueError, match="unknown utility tier"):
        build_utility_model("hiaku", max_tokens=64)  # typo -> loud failure, not silent Sonnet


def test_api_key_forwarded_from_settings():
    fake = MagicMock()
    fake.anthropic_api_key = "unit-key-abc"
    with patch("src.llm.model_factory.get_settings", return_value=fake):
        m = build_langchain_model("claude-haiku-4-5-20251001", max_tokens=64)
    # langchain_anthropic stores it as a SecretStr on .anthropic_api_key
    assert m.anthropic_api_key.get_secret_value() == "unit-key-abc"


def test_api_key_omitted_when_settings_empty():
    fake = MagicMock()
    fake.anthropic_api_key = ""
    with patch("src.llm.model_factory.get_settings", return_value=fake):
        m = build_langchain_model("claude-haiku-4-5-20251001", max_tokens=64)
    assert m is not None  # empty key not forced; ChatAnthropic's own resolver runs
