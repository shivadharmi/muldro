"""Unit tests for Connection Context Adapter enforcement helpers.

Pure-function tests: no DB, no I/O, no mocks needed.
"""

import pytest

from src.adapter.enforcement import (
    ActionNotAllowed,
    ensure_action_allowed,
    force_connection_name,
    strip_secrets,
)


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
    ensure_action_allowed("gmail.search")
    ensure_action_allowed("gmail.send")


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
