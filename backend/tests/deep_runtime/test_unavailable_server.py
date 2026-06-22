"""Unit tests for deep_runtime.middleware.unavailable_server.

Characterizes the per-turn "MCP server returned auth_required → stop retrying it
this turn" circuit breaker re-homed onto a LangChain ``wrap_tool_call`` hook.

No live API/DB: the registry server-resolution is injected via ``resolve_server``
and the tool handler is a plain async stub returning ``ToolMessage`` objects whose
``content`` carries the JSON-encoded tool payload (mirroring the real runtime).
"""

from __future__ import annotations

import json

import pytest
from langchain.agents.middleware.types import ToolCallRequest
from langchain_core.messages import ToolMessage

from src.deep_runtime.middleware.unavailable_server import (
    _UNAVAILABLE_STEER,
    make_unavailable_server_middleware,
)


def _request(name: str, call_id: str) -> ToolCallRequest:
    """Build a minimal ToolCallRequest with the only field the hook reads."""
    return ToolCallRequest(
        tool_call={"name": name, "args": {}, "id": call_id},
        tool=None,
        state={},
        runtime=None,
    )


def _auth_required_message(call_id: str, *, server: str) -> ToolMessage:
    """A ToolMessage whose JSON content is the structured auth_required envelope."""
    return ToolMessage(
        content=json.dumps(
            {
                "status": "error",
                "error_code": "auth_required",
                "server": server,
                "error": "Token expired",
            }
        ),
        tool_call_id=call_id,
        status="error",
    )


def _ok_message(call_id: str) -> ToolMessage:
    return ToolMessage(
        content=json.dumps({"status": "ok", "data": "fine"}),
        tool_call_id=call_id,
        status="success",
    )


def _hook(middleware):
    """Pull the bound async wrap_tool_call hook off the built middleware."""
    return lambda request, handler: middleware.awrap_tool_call(request, handler)


async def test_auth_required_records_server_and_appends_steer():
    """Case 1: handler returns auth_required envelope → server recorded + steer appended."""
    server_map = {"search_messages": "google-workspace"}
    middleware = make_unavailable_server_middleware(
        workspace_id="ws_1",
        db_factory=None,
        resolve_server=lambda name: server_map.get(name),
    )
    hook = _hook(middleware)

    calls: list[str] = []

    async def handler(req: ToolCallRequest) -> ToolMessage:
        calls.append(req.tool_call["name"])
        return _auth_required_message(req.tool_call["id"], server="google-workspace")

    result = await hook(_request("search_messages", "c1"), handler)

    # Handler ran (first time we don't know the server is down).
    assert calls == ["search_messages"]
    # The auth_required envelope flows through, now carrying the terminal steer.
    assert isinstance(result, ToolMessage)
    assert _UNAVAILABLE_STEER in result.content
    assert '"error_code": "auth_required"' in result.content


async def test_second_tool_same_server_is_short_circuited():
    """Case 2: a SECOND tool on the same down server skips the handler entirely."""
    server_map = {
        "search_messages": "google-workspace",
        "send_email": "google-workspace",
    }
    middleware = make_unavailable_server_middleware(
        workspace_id="ws_1",
        db_factory=None,
        resolve_server=lambda name: server_map.get(name),
    )
    hook = _hook(middleware)

    calls: list[str] = []

    async def handler(req: ToolCallRequest) -> ToolMessage:
        calls.append(req.tool_call["name"])
        return _auth_required_message(req.tool_call["id"], server="google-workspace")

    # First call marks google-workspace as down.
    await hook(_request("search_messages", "c1"), handler)
    # Second call on the same server must be short-circuited (handler NOT called).
    result = await hook(_request("send_email", "c2"), handler)

    assert calls == ["search_messages"]  # send_email never reached the handler
    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    assert result.tool_call_id == "c2"
    cached = json.loads(result.content.replace(_UNAVAILABLE_STEER, ""))
    assert cached["error_code"] == "auth_required"
    assert _UNAVAILABLE_STEER in result.content


async def test_healthy_server_still_executes():
    """Case 3: a tool on a DIFFERENT, healthy server still runs."""
    server_map = {
        "search_messages": "google-workspace",
        "list_repos": "github",
    }
    middleware = make_unavailable_server_middleware(
        workspace_id="ws_1",
        db_factory=None,
        resolve_server=lambda name: server_map.get(name),
    )
    hook = _hook(middleware)

    calls: list[str] = []

    async def handler(req: ToolCallRequest) -> ToolMessage:
        calls.append(req.tool_call["name"])
        if req.tool_call["name"] == "search_messages":
            return _auth_required_message(req.tool_call["id"], server="google-workspace")
        return _ok_message(req.tool_call["id"])

    # Down the google server first.
    await hook(_request("search_messages", "c1"), handler)
    # A github tool is healthy → handler must still run, result untouched.
    result = await hook(_request("list_repos", "c2"), handler)

    assert calls == ["search_messages", "list_repos"]
    assert isinstance(result, ToolMessage)
    assert result.status == "success"
    assert _UNAVAILABLE_STEER not in result.content
    assert json.loads(result.content)["status"] == "ok"


async def test_fresh_middleware_has_empty_sets_per_turn():
    """Case 4: a fresh middleware instance does not inherit prior down-servers."""
    server_map = {"send_email": "google-workspace"}

    def build():
        return make_unavailable_server_middleware(
            workspace_id="ws_1",
            db_factory=None,
            resolve_server=lambda name: server_map.get(name),
        )

    # Turn A: mark google-workspace down by returning auth_required for send_email.
    mw_a = build()
    hook_a = _hook(mw_a)

    async def down_handler(req: ToolCallRequest) -> ToolMessage:
        return _auth_required_message(req.tool_call["id"], server="google-workspace")

    await hook_a(_request("send_email", "a1"), down_handler)

    # Turn B: a brand-new middleware must NOT short-circuit — its sets are empty.
    mw_b = build()
    hook_b = _hook(mw_b)
    calls: list[str] = []

    async def ok_handler(req: ToolCallRequest) -> ToolMessage:
        calls.append(req.tool_call["name"])
        return _ok_message(req.tool_call["id"])

    result = await hook_b(_request("send_email", "b1"), ok_handler)

    assert calls == ["send_email"]  # handler ran → not short-circuited
    assert result.status == "success"


async def test_provider_fallback_short_circuits_when_server_unresolved():
    """Provider fallback: a tool whose server can't be resolved but whose NAME
    embeds the provider still short-circuits once that provider is down."""
    middleware = make_unavailable_server_middleware(
        workspace_id="ws_1",
        db_factory=None,
        # resolve_server only knows the first tool's server; the second is unknown.
        resolve_server=lambda name: "google-workspace" if name == "search_messages" else None,
    )
    hook = _hook(middleware)

    calls: list[str] = []

    async def handler(req: ToolCallRequest) -> ToolMessage:
        calls.append(req.tool_call["name"])
        return _auth_required_message(req.tool_call["id"], server="google-workspace")

    # First call records BOTH server (google-workspace) and provider (google).
    await hook(_request("search_messages", "c1"), handler)
    # Second tool: server unresolved, but the name carries "gmail" → provider=google
    # → provider_for_server("search_gmail_threads") == "google" → short-circuit.
    result = await hook(_request("search_gmail_threads", "c2"), handler)

    assert calls == ["search_messages"]  # provider fallback short-circuited #2
    assert _UNAVAILABLE_STEER in result.content


def test_factory_returns_agent_middleware():
    from langchain.agents.middleware import AgentMiddleware

    middleware = make_unavailable_server_middleware(
        workspace_id="ws_1",
        db_factory=None,
        resolve_server=lambda name: None,
    )
    assert isinstance(middleware, AgentMiddleware)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
