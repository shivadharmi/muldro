"""handle_execute_action selects its enforcement profile from the ACTION.

One adapter process serves a reviewed SET of providers (spec decision D2),
not a single hardcoded one. There is no process-level "which provider is
this adapter" setting to consult: every action resolves its OWN profile from
its OWN action_id via ``profile_for_action`` (membership in the registry,
never a first-dot split), and that profile's ``provider_id`` is what gets
enforced (allowlist + capability map) and what gets passed to
``resolve_connection``.

This is the fix for the wrong-connection bug: before this change the
handler looked up a single ``settings.gateway_provider``-configured profile
for every action, so a ``googlecalendar.*`` action in a multi-provider
process would have resolved (and enforced) the same principal's WRONG-PROVIDER
connection -- whatever provider the process happened to be configured for, e.g.
gmail. Never another tenant's: ``connection_resolver.resolve_connection``
always filters on tenant_id AND principal_id. And it was latent, not live:
only the gmail profile was registered before this wave, so no second provider
existed to mis-resolve.

These tests prove the profile now follows the action across all three
registered providers (gmail, googlecalendar, github), that an unregistered
action fails closed before any DB/network work, and that a process-level
``gateway_provider`` setting (if one even still exists) has no bearing on
which connection gets resolved.
"""

import inspect
from unittest.mock import AsyncMock, patch

import pytest

import src.adapter.server as server_module
from src.adapter.enforcement import ActionNotAllowed, CapabilityDenied
from src.adapter.server import handle_execute_action
from src.integrations.gateway_actions import PROVIDER_REGISTRY
from src.orchestrator.platform_jwt import mint_platform_jwt


def _token(capabilities):
    return mint_platform_jwt(
        principal_id="usr_x",
        tenant_id="ws_x",
        workspace_id="ws_x",
        capabilities=capabilities,
    )


def _first_action(provider_id: str):
    return PROVIDER_REGISTRY[provider_id].actions[0]


async def test_calendar_action_resolves_the_calendar_connection():
    """The wrong-connection bug: a calendar action must NOT resolve gmail's connection."""
    action = _first_action("googlecalendar")
    token = _token([action.capability])
    with (
        patch(
            "src.adapter.server.resolve_connection",
            new_callable=AsyncMock,
            return_value="googlecalendar:usr_x",
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
            args={"actionId": action.action_id, "input": {}},
        )

    assert result["content"] == [{"type": "text", "text": "ok"}]
    assert mock_resolve.await_args.kwargs["provider_id"] == "googlecalendar"
    mock_call.assert_awaited_once()


async def test_github_action_resolves_the_github_connection():
    action = _first_action("github")
    token = _token([action.capability])
    with (
        patch(
            "src.adapter.server.resolve_connection",
            new_callable=AsyncMock,
            return_value="github:usr_x",
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
            args={"actionId": action.action_id, "input": {}},
        )

    assert result["content"] == [{"type": "text", "text": "ok"}]
    assert mock_resolve.await_args.kwargs["provider_id"] == "github"
    mock_call.assert_awaited_once()


async def test_gmail_action_still_resolves_gmail():
    """Regression guard for the already-working path."""
    action = _first_action("gmail")
    token = _token([action.capability])
    with (
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
            args={"actionId": action.action_id, "input": {}},
        )

    assert result["content"] == [{"type": "text", "text": "ok"}]
    assert mock_resolve.await_args.kwargs["provider_id"] == "gmail"
    mock_call.assert_awaited_once()


async def test_unknown_action_is_denied_before_any_connection_lookup():
    """Proves membership-based resolution: a caller-supplied prefix cannot pick a profile."""
    token = _token(["email.search"])
    with (
        patch(
            "src.adapter.server.resolve_connection",
            new_callable=AsyncMock,
        ) as mock_resolve,
        patch(
            "src.adapter.server.call_openconnector",
            new_callable=AsyncMock,
        ) as mock_call,
    ):
        with pytest.raises(ActionNotAllowed):
            await handle_execute_action(
                None,
                token=token,
                args={"actionId": "gmail.attacker_action", "input": {}},
            )

    mock_resolve.assert_not_awaited()
    mock_call.assert_not_awaited()


async def test_dispatch_ignores_the_process_level_gateway_provider_setting():
    """server.py no longer reads ``get_settings``/``gateway_provider`` at all.

    The profile-selection setting was deleted from the module entirely, so
    there is nothing left to patch to prove indifference -- we assert the
    absence directly (the strongest available statement) and then confirm a
    gmail action still succeeds with no such patch in place.
    """
    assert "get_settings" not in inspect.getsource(server_module)
    assert "gateway_provider" not in inspect.getsource(server_module)

    action = _first_action("gmail")
    token = _token([action.capability])
    with (
        patch(
            "src.adapter.server.resolve_connection",
            new_callable=AsyncMock,
            return_value="gmail:usr_x",
        ),
        patch(
            "src.adapter.server.call_openconnector",
            new_callable=AsyncMock,
            return_value={"content": [{"type": "text", "text": "ok"}]},
        ) as mock_call,
    ):
        result = await handle_execute_action(
            None,
            token=token,
            args={"actionId": action.action_id, "input": {}},
        )

    assert result["content"] == [{"type": "text", "text": "ok"}]
    mock_call.assert_awaited_once()


async def test_cross_provider_capability_is_denied():
    """An email-scoped token dispatching a github action is denied -- capability scoping."""
    action = _first_action("github")
    token = _token(["email.search"])
    with patch(
        "src.adapter.server.call_openconnector",
        new_callable=AsyncMock,
    ) as mock_call:
        with pytest.raises(CapabilityDenied):
            await handle_execute_action(
                None,
                token=token,
                args={"actionId": action.action_id, "input": {}},
            )

    mock_call.assert_not_awaited()
