"""P3c: the interactive chat handler substitutes the per-workspace default when the request omits
permission_mode; an explicit per-turn value wins. Resolution is at the handler, NOT _process_core
(pinned callers never receive a workspace-default-derived value)."""

from __future__ import annotations

import pytest

from src.api.routes_chat import _resolve_request_permission_mode

pytestmark = pytest.mark.asyncio


class _Factory:
    """Async-context factory yielding a fake session whose Workspace has the given default."""

    def __init__(self, value):
        self._value = value

    def __call__(self):
        return self

    async def __aenter__(self):
        value = self._value

        class _DB:
            async def get(self, _model, _key):
                ws = type("_WS", (), {})()
                ws.settings = {"default_permission_mode": value} if value else None
                return ws

        return _DB()

    async def __aexit__(self, *_a):
        return False


async def test_explicit_value_wins():
    assert await _resolve_request_permission_mode("bypass", _Factory("ask"), "ws_1") == "bypass"


async def test_none_falls_back_to_workspace_default():
    assert await _resolve_request_permission_mode(None, _Factory("ask"), "ws_1") == "ask"


async def test_none_and_no_default_is_auto():
    assert await _resolve_request_permission_mode(None, _Factory(None), "ws_1") == "auto"
