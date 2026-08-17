"""Unit tests for Connection Context Adapter enforcement helpers.

Pure-function tests: no DB, no I/O, no mocks needed.
"""

import pytest

from src.adapter.enforcement import (
    ActionNotAllowed,
    CapabilityDenied,
    ensure_action_allowed,
    ensure_capability_allowed,
    force_connection_name,
    get_gateway_profile,
    profile_for_action,
    strip_secrets,
)
from src.integrations.gateway_actions.gmail import GMAIL_ACTIONS

GMAIL_PROFILE = get_gateway_profile("gmail")
GMAIL_ACTION_ALLOWLIST = GMAIL_PROFILE.action_allowlist


def test_gmail_profile_is_derived_from_the_table():
    assert GMAIL_PROFILE.action_allowlist == frozenset(a.action_id for a in GMAIL_ACTIONS)
    for a in GMAIL_ACTIONS:
        assert GMAIL_PROFILE.action_required_capability[a.action_id] == a.capability


CURATED = {
    "gmail.get_profile": "email.read",
    "gmail.fetch_emails": "email.search",
    "gmail.search_threads": "email.search",
    "gmail.get_message": "email.read",
    "gmail.list_threads": "email.list",
    "gmail.list_labels": "email.list",
    "gmail.send_email": "email.send",
}


def test_allowlist_is_the_curated_seven_real_actions():
    assert GMAIL_ACTION_ALLOWLIST == frozenset(CURATED)


def test_every_allowlisted_action_maps_to_its_capability():
    for action_id, cap in CURATED.items():
        assert GMAIL_PROFILE.action_required_capability[action_id] == cap


def test_read_scoped_principal_cannot_send_email():
    with pytest.raises(CapabilityDenied):
        ensure_capability_allowed("gmail.send_email", ("email.read",), GMAIL_PROFILE)


def test_force_connection_name_overwrites_attacker_supplied_value_and_leaves_input_untouched():
    # OpenConnector's execute_action reads the camelCase key `connectionName`
    # (confirmed via the Task 0 spike), so the forced value must land there.
    input_args = {"connectionName": "attacker_controlled", "query": "is:unread"}

    result = force_connection_name(input_args, "gateway_forced_connection")

    assert result["connectionName"] == "gateway_forced_connection"
    assert result["query"] == "is:unread"
    # Original dict must be untouched (immutability / no mutation of caller's data).
    assert input_args["connectionName"] == "attacker_controlled"


def test_ensure_action_allowed_permits_allowlisted_actions():
    ensure_action_allowed("gmail.fetch_emails", GMAIL_PROFILE)
    ensure_action_allowed("gmail.send_email", GMAIL_PROFILE)


def test_ensure_action_allowed_raises_for_disallowed_action():
    with pytest.raises(ActionNotAllowed):
        ensure_action_allowed("gmail.delete_forever", GMAIL_PROFILE)


def test_strip_secrets_removes_nested_secret_keys_but_keeps_benign_fields():
    obj = {
        "access_token": "secret-abc",
        "profile": {
            "email": "user@example.com",
            "refresh_token": "secret-def",
        },
        "messages": [
            {"id": "msg_1", "authorization": "Bearer xyz"},
            {"id": "msg_2", "subject": "hello"},
        ],
    }

    cleaned = strip_secrets(obj)

    assert "access_token" not in cleaned
    assert "refresh_token" not in cleaned["profile"]
    assert cleaned["profile"]["email"] == "user@example.com"
    assert "authorization" not in cleaned["messages"][0]
    assert cleaned["messages"][0]["id"] == "msg_1"
    assert cleaned["messages"][1] == {"id": "msg_2", "subject": "hello"}


def test_strip_secrets_catches_camelcase_and_kebab_variants():
    # OpenConnector speaks camelCase (spike): a snake_case-only matcher would
    # leak these. Normalized matching must drop them all.
    obj = {
        "accessToken": "a",
        "refreshToken": "b",
        "idToken": "c",
        "clientSecret": "d",
        "apiKey": "e",
        "access-token": "f",
        "keep": "ok",
    }
    cleaned = strip_secrets(obj)
    assert cleaned == {"keep": "ok"}


def test_force_connection_name_rejects_empty_forced_name():
    # An empty connectionName makes OpenConnector fall back to its default
    # connection — a cross-tenant path in the shared-instance model. Fail closed.
    with pytest.raises(ValueError):
        force_connection_name({"actionId": "gmail.fetch_emails"}, "")
    with pytest.raises(ValueError):
        force_connection_name({"actionId": "gmail.fetch_emails"}, "   ")


def test_profile_is_derived_from_the_registry():
    profile = get_gateway_profile("github")
    assert profile.provider_id == "github"
    assert profile.actions
    # allowlist and capability map are DERIVED, never separately declared
    assert profile.action_allowlist == frozenset(a.action_id for a in profile.actions)
    assert profile.action_required_capability == {
        a.action_id: a.capability for a in profile.actions
    }


def test_profile_for_action_resolves_per_call():
    assert profile_for_action("gmail.get_profile").provider_id == "gmail"


def test_profile_for_action_fails_closed_on_unknown_action():
    with pytest.raises(ActionNotAllowed):
        profile_for_action("gmail.attacker_action")


def test_cross_provider_token_is_denied():
    """An email-scoped principal cannot invoke a github action."""
    github = get_gateway_profile("github")
    action = github.actions[0].action_id
    with pytest.raises(CapabilityDenied):
        ensure_capability_allowed(action, ("email.read", "email.send"), github)


def test_ensure_action_allowed_requires_an_explicit_profile():
    import inspect

    sig = inspect.signature(ensure_action_allowed)
    assert sig.parameters["profile"].default is inspect.Parameter.empty
