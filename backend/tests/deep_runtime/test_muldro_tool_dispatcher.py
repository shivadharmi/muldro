"""Step 6A.5: muldro_tool_dispatcher routes Muldro tool calls through execute_tool
(short-circuiting the shell), normalizes error/blocked to status="error", and falls through
for deepagents built-ins.

Tests drive the interceptor directly via ``mw.awrap_tool_call`` (the same pattern as
``test_capability_scope.py``) — no live Anthropic API, no DB required.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import ToolMessage

from src.deep_runtime.middleware.muldro_tool_dispatcher import make_muldro_tool_dispatcher

USER_ID = "u_test"
WORKSPACE_ID = "ws_test"


def _request(
    tool_name: str, args: dict | None = None, call_id: str = "call_123"
) -> SimpleNamespace:
    """Minimal ToolCallRequest stand-in: only ``.tool_call`` is read."""
    return SimpleNamespace(tool_call={"name": tool_name, "args": args or {}, "id": call_id})


def _hook(mw):
    """Extract the async wrap-tool-call hook from the middleware instance."""
    return mw.awrap_tool_call


@pytest.fixture
def handler():
    """AsyncMock standing in for the downstream tool executor."""
    h = AsyncMock(name="handler")
    h.return_value = ToolMessage(content="executed", tool_call_id="call_123")
    return h


# ---------------------------------------------------------------------------
# Test 1: Muldro tool is dispatched through execute_tool; shell body never runs
# ---------------------------------------------------------------------------


async def test_dispatches_muldro_tool_to_execute_tool(handler):
    """A Muldro tool call goes through execute_tool; handler (shell body) is never invoked."""
    calls: list[tuple[str, dict, str, str]] = []

    async def fake_execute_tool(name: str, args: dict, uid: str, ws: str) -> dict:
        calls.append((name, args, uid, ws))
        return {"result": "ok", "data": 42}

    mw = make_muldro_tool_dispatcher(
        execute_tool=fake_execute_tool, user_id=USER_ID, workspace_id=WORKSPACE_ID
    )
    result = await _hook(mw)(_request("search_memories", {"query": "foo"}, "call_abc"), handler)

    # handler (shell body) must NOT have been called
    handler.assert_not_awaited()
    # execute_tool must have been called with the recovered name + args
    assert len(calls) == 1
    assert calls[0][0] == "search_memories"
    assert calls[0][1] == {"query": "foo"}
    assert calls[0][2] == USER_ID
    assert calls[0][3] == WORKSPACE_ID
    # result must be a ToolMessage
    assert isinstance(result, ToolMessage)


# ---------------------------------------------------------------------------
# Test 2: error result → ToolMessage(status="error") with correct id + name
# ---------------------------------------------------------------------------


async def test_error_result_maps_to_status_error(handler):
    """A dict with 'error' key → ToolMessage(status='error') with correct tool_call_id and name."""

    async def fake_execute_tool(name, args, uid, ws):
        return {"error": "nope, something broke"}

    mw = make_muldro_tool_dispatcher(
        execute_tool=fake_execute_tool, user_id=USER_ID, workspace_id=WORKSPACE_ID
    )
    result = await _hook(mw)(_request("send_email", {}, "call_err"), handler)

    handler.assert_not_awaited()
    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    assert result.tool_call_id == "call_err"
    assert result.name == "send_email"
    payload = json.loads(result.content)
    assert payload["error"] == "nope, something broke"


# ---------------------------------------------------------------------------
# Test 3: blocked=True result also maps to status="error"
# ---------------------------------------------------------------------------


async def test_blocked_result_maps_to_status_error(handler):
    """A dict with 'blocked': True (and 'error') → ToolMessage(status='error')."""

    async def fake_execute_tool(name, args, uid, ws):
        return {"error": "blocked by policy", "blocked": True}

    mw = make_muldro_tool_dispatcher(
        execute_tool=fake_execute_tool, user_id=USER_ID, workspace_id=WORKSPACE_ID
    )
    result = await _hook(mw)(_request("delete_file", {}, "call_blk"), handler)

    handler.assert_not_awaited()
    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    assert result.tool_call_id == "call_blk"
    assert result.name == "delete_file"


# ---------------------------------------------------------------------------
# Test 4: successful dict result → ToolMessage(status="success") with JSON content
# ---------------------------------------------------------------------------


async def test_success_result_maps_to_status_success(handler):
    """A successful dict result → ToolMessage(status='success') with json.dumps content."""

    async def fake_execute_tool(name, args, uid, ws):
        return {"items": [1, 2, 3], "total": 3}

    mw = make_muldro_tool_dispatcher(
        execute_tool=fake_execute_tool, user_id=USER_ID, workspace_id=WORKSPACE_ID
    )
    result = await _hook(mw)(_request("list_events", {"limit": 10}, "call_suc"), handler)

    handler.assert_not_awaited()
    assert isinstance(result, ToolMessage)
    assert result.status == "success"
    assert result.tool_call_id == "call_suc"
    assert result.name == "list_events"
    payload = json.loads(result.content)
    assert payload == {"items": [1, 2, 3], "total": 3}


# ---------------------------------------------------------------------------
# Test 5: deepagents built-in falls through to handler; execute_tool not called
# ---------------------------------------------------------------------------


async def test_builtin_write_todos_falls_through_to_handler(handler):
    """A deepagents built-in (write_todos) must fall through to handler; execute_tool NOT called."""
    execute_called: list[str] = []

    async def fake_execute_tool(name, args, uid, ws):
        execute_called.append(name)
        return {"error": "should not be called"}

    mw = make_muldro_tool_dispatcher(
        execute_tool=fake_execute_tool, user_id=USER_ID, workspace_id=WORKSPACE_ID
    )
    result = await _hook(mw)(_request("write_todos", {"todos": []}, "call_todo"), handler)

    # handler must have been called (fall-through)
    handler.assert_awaited_once()
    assert result is handler.return_value
    # execute_tool must NOT have been called
    assert execute_called == []


# ---------------------------------------------------------------------------
# Test 6: ALL built-ins fall through (parametrize over DEEPAGENTS_BUILTIN_NAMES)
# ---------------------------------------------------------------------------


async def test_all_builtins_fall_through(handler):
    """Every name in DEEPAGENTS_BUILTIN_NAMES falls through; execute_tool never called."""
    from src.deep_runtime.builtins import DEEPAGENTS_BUILTIN_NAMES

    execute_called: list[str] = []

    async def fake_execute_tool(name, args, uid, ws):
        execute_called.append(name)
        return {"error": "should not be called"}

    for builtin_name in sorted(DEEPAGENTS_BUILTIN_NAMES):
        handler.reset_mock()
        mw = make_muldro_tool_dispatcher(
            execute_tool=fake_execute_tool, user_id=USER_ID, workspace_id=WORKSPACE_ID
        )
        result = await _hook(mw)(_request(builtin_name, {}, "call_bi"), handler)

        assert handler.await_count == 1, f"{builtin_name!r} did not fall through to handler"
        assert result is handler.return_value, f"{builtin_name!r} did not return handler result"

    assert execute_called == [], f"execute_tool was called for built-ins: {execute_called}"


# ---------------------------------------------------------------------------
# Test 7: string result (non-dict) is stringified as content with status=success
# ---------------------------------------------------------------------------


async def test_string_result_is_stringified(handler):
    """A non-dict result (e.g. a plain string) is str()-ed into ToolMessage.content."""

    async def fake_execute_tool(name, args, uid, ws):
        return "plain string result"

    mw = make_muldro_tool_dispatcher(
        execute_tool=fake_execute_tool, user_id=USER_ID, workspace_id=WORKSPACE_ID
    )
    result = await _hook(mw)(_request("some_tool", {}, "call_str"), handler)

    handler.assert_not_awaited()
    assert isinstance(result, ToolMessage)
    assert result.status == "success"
    assert result.content == "plain string result"


# ---------------------------------------------------------------------------
# Test 8: user_id + workspace_id from closure, NOT from tool_call args
# ---------------------------------------------------------------------------


async def test_user_and_workspace_from_closure_not_args(handler):
    """user_id/workspace_id are injected from closure, never from tool_call args."""
    captured: list[tuple[str, str]] = []

    async def fake_execute_tool(name, args, uid, ws):
        captured.append((uid, ws))
        return {"ok": True}

    # Put fake uid/ws in the args — they must be ignored
    mw = make_muldro_tool_dispatcher(
        execute_tool=fake_execute_tool, user_id="real_user", workspace_id="real_ws"
    )
    await _hook(mw)(
        _request("some_tool", {"user_id": "evil", "workspace_id": "evil_ws"}, "call_x"),
        handler,
    )

    assert captured == [("real_user", "real_ws")]
