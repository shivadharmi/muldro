from unittest.mock import AsyncMock, patch

import run_adapter


async def test_warm_start_registers_the_curated_actions_on_the_module_adapter():
    # hybrid: schema is hand-typed; the live guide is only a drift signal, so an
    # empty guide is fine here — we assert the named tools are registered.
    fetcher = AsyncMock(return_value={})
    with patch("run_adapter.get_action_guide", fetcher):
        await run_adapter.warm_start()

    names = {t.name for t in await run_adapter.adapter.list_tools()}
    assert "gmail.get_profile" in names
    assert "gmail.send_email" in names
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
