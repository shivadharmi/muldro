"""Unit tests for Connection Context Adapter enforcement helpers.

Pure-function tests: no DB, no I/O, no mocks needed.
"""

import pytest

from src.adapter.enforcement import (
    GMAIL_ACTION_ALLOWLIST,
    GMAIL_PROFILE,
    ActionNotAllowed,
    CapabilityDenied,
    ensure_action_allowed,
    ensure_capability_allowed,
    force_connection_name,
    strip_secrets,
)

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
    ensure_action_allowed("gmail.fetch_emails")
    ensure_action_allowed("gmail.send_email")


def test_ensure_action_allowed_raises_for_disallowed_action():
    with pytest.raises(ActionNotAllowed):
        ensure_action_allowed("gmail.delete_forever")


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
