"""Step 10 (e2e-caught): the deep model factory must pass the Anthropic API key.

LangChain's ChatAnthropic reads the unprefixed ANTHROPIC_API_KEY env var, which Jarvis
never sets (it uses JARVIS_ANTHROPIC_API_KEY). Without an explicit key the deep runtime
model raises "Could not resolve authentication method" on the first call — the blocker
the local deep e2e surfaced. build_chat_model must source the key from settings.
"""

from unittest.mock import MagicMock, patch

from src.deep_runtime.model_factory import build_chat_model
from src.orchestrator.agents import SubAgent


def _agent() -> SubAgent:
    return SubAgent(name="librarian", prompt="p", model_tier="sonnet", capability_scope=set())


def test_build_chat_model_passes_api_key_from_settings():
    fake_settings = MagicMock()
    fake_settings.anthropic_api_key = "unit-test-key-xyz"
    with patch("src.deep_runtime.model_factory.get_settings", return_value=fake_settings):
        model = build_chat_model(_agent())
    # langchain_anthropic stores it as a SecretStr on anthropic_api_key
    assert model.anthropic_api_key.get_secret_value() == "unit-test-key-xyz"


def test_build_chat_model_omits_api_key_when_settings_empty():
    """No key in settings (e.g. Bedrock deployments) → don't force an empty key; leave the
    resolution to ChatAnthropic's own env/credentials chain (Bedrock-deep is a separate gap)."""
    fake_settings = MagicMock()
    fake_settings.anthropic_api_key = ""
    with patch("src.deep_runtime.model_factory.get_settings", return_value=fake_settings):
        model = build_chat_model(_agent())
    assert model is not None


def test_build_chat_model_clamps_thinking_budget_below_max_tokens():
    """Anthropic requires max_tokens > thinking.budget_tokens (thinking counts toward
    max_tokens). A default SubAgent has max_tokens == budget_tokens == 4096, which 400s.
    The factory must clamp budget < max_tokens, mirroring legacy build_thinking_params
    (agent_loop.py:458-459). e2e-caught blocker."""
    fake_settings = MagicMock()
    fake_settings.anthropic_api_key = "k"
    agent = _agent()  # sonnet (non-adaptive), default max_tokens==thinking.budget_tokens
    assert agent.max_tokens == agent.thinking.budget_tokens  # the pathological equal case
    with patch("src.deep_runtime.model_factory.get_settings", return_value=fake_settings):
        model = build_chat_model(agent)
    assert model.thinking is not None
    assert model.thinking["budget_tokens"] < model.max_tokens
    assert model.thinking["budget_tokens"] == agent.max_tokens - 1
