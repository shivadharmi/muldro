"""handle_execute_action selects its enforcement profile from settings.

The adapter is no longer hard-locked to Gmail: it reads ``gateway_provider``
and enforces that provider's allowlist/capability-map and resolves that
provider's connection. Default is gmail (covered by the existing tests); here
we prove the hackernews profile path and that the wrong provider is rejected.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from src.adapter.enforcement import ActionNotAllowed
from src.adapter.server import handle_execute_action
from src.orchestrator.platform_jwt import mint_platform_jwt


def _token(capabilities):
    return mint_platform_jwt(
        principal_id="usr_x",
        tenant_id="ws_x",
        workspace_id="ws_x",
        capabilities=capabilities,
    )


async def test_hackernews_provider_allows_hackernews_action():
    token = _token(["hackernews.read"])
    with (
        patch(
            "src.adapter.server.get_settings",
            return_value=SimpleNamespace(gateway_provider="hackernews"),
        ),
        patch(
            "src.adapter.server.resolve_connection",
            new_callable=AsyncMock,
            return_value="default",
        ) as mock_resolve,
        patch(
            "src.adapter.server.call_openconnector",
            new_callable=AsyncMock,
            return_value={"content": [{"type": "text", "text": "ok"}]},
        ) as mock_call,
    ):
        result = await handle_execute_action(
            None,
            token=token,
            args={"actionId": "hackernews.get_ask_stories", "input": {}},
        )

    assert result["content"] == [{"type": "text", "text": "ok"}]
    # Resolved the HACKERNEWS provider connection, not gmail.
    assert mock_resolve.await_args.kwargs["provider_id"] == "hackernews"
    mock_call.assert_awaited_once()


async def test_gmail_default_still_rejects_hackernews_action():
    token = _token(["hackernews.read"])
    with (
        patch(
            "src.adapter.server.get_settings",
            return_value=SimpleNamespace(gateway_provider="gmail"),
        ),
        patch("src.adapter.server.call_openconnector", new_callable=AsyncMock) as mock_call,
    ):
        with pytest.raises(ActionNotAllowed):
            await handle_execute_action(
                None,
                token=token,
                args={"actionId": "hackernews.get_ask_stories", "input": {}},
            )
    mock_call.assert_not_awaited()
