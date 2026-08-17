from unittest.mock import AsyncMock, patch

import run_adapter
from src.config.settings import get_settings
from src.integrations.gateway_naming import action_id_to_tool_name


async def _noop_guide(action_id: str) -> dict:
    return {}


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
    from src.integrations.gateway_actions import PROVIDER_REGISTRY

    monkeypatch.setattr(run_adapter, "get_action_guide", _noop_guide)
    count = await run_adapter.warm_start()
    expected = sum(len(p.actions) for p in PROVIDER_REGISTRY.values())
    assert count == expected


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
