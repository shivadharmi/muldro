"""handle_execute_action selects its enforcement profile from settings.

The adapter is not hard-coded to one profile at the call site: it reads
``gateway_provider``, enforces that provider's allowlist/capability-map, and
resolves that provider's connection. Gmail is the only reviewed profile today,
so these tests prove the selection path itself -- the configured provider is
what gets enforced and resolved, and an unknown provider fails closed.
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


async def test_configured_provider_profile_is_enforced_and_resolved():
    token = _token(["email.search"])
    with (
        patch(
            "src.adapter.server.get_settings",
            return_value=SimpleNamespace(gateway_provider="gmail"),
        ),
        patch(
            "src.adapter.server.resolve_connection",
            new_callable=AsyncMock,
            return_value="gmail:usr_x",
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
            args={"actionId": "gmail.fetch_emails", "input": {}},
        )

    assert result["content"] == [{"type": "text", "text": "ok"}]
    # Resolved the CONFIGURED provider's connection.
    assert mock_resolve.await_args.kwargs["provider_id"] == "gmail"
    mock_call.assert_awaited_once()


async def test_action_outside_the_configured_profile_is_rejected():
    """An action from another provider is not reachable under the gmail profile."""
    token = _token(["repo.read"])
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
                args={"actionId": "github.list_issues", "input": {}},
            )
    mock_call.assert_not_awaited()


async def test_unknown_configured_provider_fails_closed():
    """A provider with no reviewed profile must abort before any OpenConnector call."""
    token = _token(["email.search"])
    with (
        patch(
            "src.adapter.server.get_settings",
            return_value=SimpleNamespace(gateway_provider="dropbox"),
        ),
        patch("src.adapter.server.call_openconnector", new_callable=AsyncMock) as mock_call,
    ):
        with pytest.raises(ValueError):
            await handle_execute_action(
                None,
                token=token,
                args={"actionId": "gmail.fetch_emails", "input": {}},
            )
    mock_call.assert_not_awaited()
