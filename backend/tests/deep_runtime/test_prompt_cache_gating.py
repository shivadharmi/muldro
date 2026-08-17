"""Task 4.3 — gate Anthropic prompt/tool caching by model capability.

``ToolExecutor.apply_cache_control_to_tools`` stamps a ``cache_control`` ephemeral
marker on the last tool def. That marker is a no-op (or an error) on non-Anthropic
providers, so a ``supports_cache`` flag must suppress it when the backing model does
not support prompt caching (``ModelSpec.supports_prompt_cache is False``). Default is
``True`` so today's Anthropic path stays byte-identical.
"""

from unittest.mock import MagicMock

from src.config.model_catalog import get_model_spec
from src.orchestrator.tool_executor import ToolExecutor

# The method under test only manipulates the ``tools`` list, so the collaborators
# (EventPublisher + db_factory provider) can be inert mocks.
tx = ToolExecutor(events=MagicMock(), db_factory_provider=MagicMock())


def test_cache_control_stripped_for_non_caching_provider():
    tools = [{"name": "t", "description": "d", "input_schema": {"type": "object"}}]
    spec = get_model_spec("openai", "gpt-5")  # supports_prompt_cache False
    out = tx.apply_cache_control_to_tools(tools, supports_cache=spec.supports_prompt_cache)
    assert all("cache_control" not in t for t in out)


def test_cache_control_kept_for_anthropic():
    tools = [{"name": "t", "description": "d", "input_schema": {"type": "object"}}]
    spec = get_model_spec("anthropic", "claude-sonnet-4-6")
    out = tx.apply_cache_control_to_tools(tools, supports_cache=spec.supports_prompt_cache)
    assert any("cache_control" in t for t in out)


def test_default_is_behavior_preserving():
    # No supports_cache kwarg → Anthropic behaviour (stamp) preserved.
    tools = [{"name": "t", "description": "d", "input_schema": {"type": "object"}}]
    out = tx.apply_cache_control_to_tools(tools)
    assert out[-1].get("cache_control") == {"type": "ephemeral"}
