"""Redaction + bounding of the persisted tool_input (single-lead cutover, invariant 9).

Secrets must never reach `Approval.artifact_refs` or the prepared-work queue, and a large
payload must never bloat the approval row.
"""

import json

from src.deep_runtime.middleware.trust_gate import (
    _MAX_PERSISTED_CONTEXT_CHARS,
    REDACTED,
    redact_tool_input,
)


def test_none_and_empty_input_serialise_to_empty_object():
    assert redact_tool_input(None) == ("{}", False)
    assert redact_tool_input({}) == ("{}", False)


def test_plain_values_survive_untouched():
    payload, truncated = redact_tool_input({"to": "a@b.com", "subject": "Hi", "count": 3})
    assert truncated is False
    assert json.loads(payload) == {"to": "a@b.com", "subject": "Hi", "count": 3}


def test_deny_list_keys_are_redacted_case_insensitively_by_substring():
    payload, _ = redact_tool_input(
        {
            "api_key": "sk-live-1",
            "AUTHORIZATION": "Bearer x",
            "refresh_token": "rt-1",
            "user_password": "hunter2",
            "client_secret": "cs-1",
            "aws_credential": "c-1",
            "subject": "keep me",
        }
    )
    obj = json.loads(payload)
    assert obj["api_key"] == REDACTED
    assert obj["AUTHORIZATION"] == REDACTED
    assert obj["refresh_token"] == REDACTED
    assert obj["user_password"] == REDACTED
    assert obj["client_secret"] == REDACTED
    assert obj["aws_credential"] == REDACTED
    assert obj["subject"] == "keep me"


def test_redaction_recurses_into_nested_dicts_and_lists():
    payload, _ = redact_tool_input(
        {"headers": {"authorization": "Bearer x"}, "items": [{"token": "t1"}, {"ok": "yes"}]}
    )
    obj = json.loads(payload)
    assert obj["headers"]["authorization"] == REDACTED
    assert obj["items"][0]["token"] == REDACTED
    assert obj["items"][1]["ok"] == "yes"


def test_oversized_payload_is_truncated_and_marked():
    payload, truncated = redact_tool_input({"body": "x" * (_MAX_PERSISTED_CONTEXT_CHARS + 500)})
    assert truncated is True
    assert len(payload) <= _MAX_PERSISTED_CONTEXT_CHARS


def test_unserialisable_values_degrade_to_repr_rather_than_raising():
    class Opaque:
        def __repr__(self):
            return "<Opaque>"

    payload, truncated = redact_tool_input({"thing": Opaque()})
    assert truncated is False
    assert "<Opaque>" in payload
