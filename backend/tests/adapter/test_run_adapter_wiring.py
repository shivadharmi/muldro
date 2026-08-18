from unittest.mock import AsyncMock, patch

import pytest

import run_adapter
from src.config.settings import get_settings
from src.integrations.gateway_actions import PROVIDER_REGISTRY
from src.integrations.gateway_naming import action_id_to_tool_name


async def _noop_guide(action_id: str) -> dict:
    return {}


def _expected_tool_names() -> set[str]:
    return {
        action_id_to_tool_name(a.action_id) for p in PROVIDER_REGISTRY.values() for a in p.actions
    }


@pytest.fixture(autouse=True)
def _isolate_module_adapter():
    """Undo warm-start's writes to the module-level ``run_adapter.adapter`` singleton.

    Unlike tests/adapter/test_warm_start.py (which builds a fresh ``FastMCP`` per
    test), these tests must exercise the real module-level adapter — so they leave
    registered tools behind for whatever runs next. Strip the gateway tools
    afterwards; the two generic decorator-registered tools (``execute_action`` /
    ``list_connections``) are part of the module's definition and stay.
    """
    yield
    provider = run_adapter.adapter.local_provider
    for name in _expected_tool_names():
        try:
            provider.remove_tool(name)
        except Exception:  # not registered by this test — nothing to undo
            pass


async def test_warm_start_registers_the_curated_actions_on_the_module_adapter():
    # hybrid: schema is hand-typed; the live guide is only a drift signal, so an
    # empty guide is fine here — we assert the named tools are registered.
    fetcher = AsyncMock(return_value={})
    with patch("run_adapter.get_action_guide", fetcher):
        await run_adapter.warm_start()

    names = {t.name for t in await run_adapter.adapter.list_tools()}
    # agent-legal (underscore) names, not the dotted OC actionIds — Anthropic/
    # OpenAI tool-calling APIs forbid dots in tool names.
    assert action_id_to_tool_name("gmail.get_profile") in names
    assert action_id_to_tool_name("gmail.send_email") in names
    assert "gmail.get_profile" not in names
    # the original generic tools are still present
    assert "execute_action" in names
    assert "list_connections" in names


def test_bearer_token_comes_from_shared_helper():
    # run_adapter must delegate to the shared helper, not keep a private copy
    import inspect

    source = inspect.getsource(run_adapter)
    assert "http_context" in source
    # directly guard against the dead private-copy regression this task removed
    assert "_bearer_token" not in source


async def test_warm_start_registers_every_provider_by_default(monkeypatch):
    monkeypatch.setattr(run_adapter, "get_action_guide", _noop_guide)
    count = await run_adapter.warm_start()

    expected = _expected_tool_names()
    assert count == sum(len(p.actions) for p in PROVIDER_REGISTRY.values())
    # The count alone is a SUM over providers: it would still match if two
    # providers collided on a tool name and add_tool silently overwrote one.
    # Assert the exact name set actually reached the adapter.
    assert expected <= {t.name for t in await run_adapter.adapter.list_tools()}


async def test_gateway_provider_setting_is_gone_and_every_server_is_registered(monkeypatch):
    # the process-level single-provider setting is deleted outright — one
    # adapter process now serves every provider in the registry, so there is
    # nothing left to select
    assert not hasattr(get_settings(), "gateway_provider")

    monkeypatch.setattr(run_adapter, "get_action_guide", _noop_guide)
    await run_adapter.warm_start()

    names = {t.name for t in await run_adapter.adapter.list_tools()}
    assert any(name.startswith("gmail_") for name in names)
    assert any(name.startswith("googlecalendar_") for name in names)
    assert any(name.startswith("github_") for name in names)
