"""P3b: the chat HTTP contract drops the user-facing legacy ``mode``; the interactive handler
forwards a fixed ``mode="ask"`` so live default behavior is byte-identical, while
``permission_mode`` is the user-facing field."""

from __future__ import annotations

import pytest

from src.api.routes_chat import ChatRequest


def test_chat_request_has_no_user_mode_field():
    # ``mode`` is removed from the request contract (retired in favor of permission_mode).
    assert "mode" not in ChatRequest.model_fields


def test_chat_request_still_has_permission_mode():
    assert "permission_mode" in ChatRequest.model_fields


def test_chat_request_ignores_a_client_sent_mode():
    # A client that still POSTs ``mode`` is not rejected; the field is simply dropped.
    req = ChatRequest.model_validate({"message": "hi", "mode": "plan"})
    assert not hasattr(req, "mode")


def test_permission_mode_is_optional_default_none():
    # P3c: the field becomes optional so the handler can substitute the per-workspace default.
    assert ChatRequest(message="hi").permission_mode is None
    assert ChatRequest(message="hi", permission_mode="bypass").permission_mode == "bypass"


def test_permission_mode_still_rejects_typos():
    with pytest.raises(ValueError):
        ChatRequest(message="hi", permission_mode="banana")
