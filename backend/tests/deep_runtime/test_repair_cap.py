"""Unit tests for deep_runtime.middleware.repair_cap.

The tool-argument repair loop (``ToolExecutor.execute_tool`` rejects bad args with
``error_code="invalid_tool_args"`` → ``muldro_tool_dispatcher`` turns that into
``ToolMessage(status="error")`` → LangGraph gives the model another turn) is real and,
before this middleware, unbounded. These tests characterize the cap.

No live API/DB: the request is a real ``ToolCallRequest`` (as in
``test_unavailable_server.py``) and the downstream handler is an autospec'd double, so
"did the short-circuit actually skip dispatch?" is assertable — which is the whole point
of the cap.
"""

from __future__ import annotations

import json
from unittest.mock import create_autospec

import pytest
from langchain.agents.middleware.types import ToolCallRequest
from langchain_core.messages import ToolMessage
from langgraph.errors import GraphInterrupt

from src.deep_runtime.builtins import DEEPAGENTS_BUILTIN_NAMES
from src.deep_runtime.middleware.repair_cap import MAX_ATTEMPTS, make_repair_cap_middleware


def _request(name: str, call_id: str = "tc1") -> ToolCallRequest:
    """Build a minimal ToolCallRequest with the only field the hook reads."""
    return ToolCallRequest(
        tool_call={"name": name, "args": {}, "id": call_id},
        tool=None,
        state={},
        runtime=None,
    )


def _invalid_args_message(call_id: str = "tc1", *, name: str = "render_surface") -> ToolMessage:
    """The exact shape muldro_tool_dispatcher produces for a rejected-args result."""
    return ToolMessage(
        content=json.dumps(
            {
                "error": "Invalid argument(s) for 'render_surface': subtitle: too long.",
                "error_code": "invalid_tool_args",
            }
        ),
        tool_call_id=call_id,
        name=name,
        status="error",
    )


def _ok_message(call_id: str = "tc1", *, name: str = "render_surface") -> ToolMessage:
    return ToolMessage(
        content=json.dumps({"ok": True}), tool_call_id=call_id, name=name, status="success"
    )


async def _reference_handler(request: ToolCallRequest) -> ToolMessage:  # pragma: no cover
    """Signature source for the autospec'd downstream handler double."""
    raise AssertionError("reference handler is a signature source only")


def _handler(*results: ToolMessage):
    """An autospec'd downstream handler returning ``results`` in order."""
    handler = create_autospec(_reference_handler)
    handler.side_effect = list(results)
    return handler


# ---------------------------------------------------------------------------
# Below the cap: every attempt is dispatched
# ---------------------------------------------------------------------------


async def test_invalid_args_failures_below_the_cap_are_dispatched():
    """The first MAX_ATTEMPTS rejections all reach the handler — repair needs a chance."""
    handler = _handler(*(_invalid_args_message() for _ in range(MAX_ATTEMPTS)))
    mw = make_repair_cap_middleware()

    for _ in range(MAX_ATTEMPTS):
        result = await mw.awrap_tool_call(_request("render_surface"), handler)
        assert result.status == "error"

    assert handler.await_count == MAX_ATTEMPTS


# ---------------------------------------------------------------------------
# At the cap: short-circuit WITHOUT dispatching
# ---------------------------------------------------------------------------


async def test_call_after_max_attempts_does_not_dispatch():
    """Once capped, the handler is NOT awaited — the cap, not just the message."""
    handler = _handler(*(_invalid_args_message() for _ in range(MAX_ATTEMPTS)))
    mw = make_repair_cap_middleware()

    for _ in range(MAX_ATTEMPTS):
        await mw.awrap_tool_call(_request("render_surface"), handler)

    handler.reset_mock()
    handler.side_effect = AssertionError("capped call must not dispatch")
    result = await mw.awrap_tool_call(_request("render_surface", "tc_capped"), handler)

    handler.assert_not_awaited()
    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    assert result.tool_call_id == "tc_capped"


async def test_capped_message_names_the_tool_and_steers_to_chat():
    """The terminal message says what is wrong AND what to do (house steer style)."""
    handler = _handler(*(_invalid_args_message() for _ in range(MAX_ATTEMPTS)))
    mw = make_repair_cap_middleware()
    for _ in range(MAX_ATTEMPTS):
        await mw.awrap_tool_call(_request("render_surface"), handler)

    handler.side_effect = AssertionError("capped call must not dispatch")
    result = await mw.awrap_tool_call(_request("render_surface", "tc_capped"), handler)

    payload = json.loads(result.content)
    text = payload["error"]
    assert "render_surface" in text
    assert str(MAX_ATTEMPTS) in text
    assert "chat" in text.lower()
    assert payload["error_code"] == "repair_cap_exceeded"


# ---------------------------------------------------------------------------
# The cap is per tool name, not per turn
# ---------------------------------------------------------------------------


async def test_cap_is_keyed_per_tool_name():
    """Capping render_surface must not cap an unrelated tool in the same turn."""
    handler = _handler(*(_invalid_args_message() for _ in range(MAX_ATTEMPTS)))
    mw = make_repair_cap_middleware()
    for _ in range(MAX_ATTEMPTS):
        await mw.awrap_tool_call(_request("render_surface"), handler)

    handler.reset_mock()
    handler.side_effect = [_ok_message("tc_other", name="search_memories")]
    result = await mw.awrap_tool_call(_request("search_memories", "tc_other"), handler)

    handler.assert_awaited_once()
    assert result.status == "success"


# ---------------------------------------------------------------------------
# A repaired call resets the tally
# ---------------------------------------------------------------------------


async def test_successful_call_resets_the_failure_count():
    """A non-error result is a repair: the tool gets its full budget again."""
    mw = make_repair_cap_middleware()
    handler = _handler(
        *(_invalid_args_message() for _ in range(MAX_ATTEMPTS - 1)),
        _ok_message(),
        *(_invalid_args_message() for _ in range(MAX_ATTEMPTS)),
    )

    total = (MAX_ATTEMPTS - 1) + 1 + MAX_ATTEMPTS
    for _ in range(total):
        await mw.awrap_tool_call(_request("render_surface"), handler)

    # Every one of those was dispatched — the success in the middle wiped the tally.
    assert handler.await_count == total

    # And only now, MAX_ATTEMPTS failures after the reset, is the tool capped.
    handler.reset_mock()
    handler.side_effect = AssertionError("capped call must not dispatch")
    result = await mw.awrap_tool_call(_request("render_surface", "tc_capped"), handler)
    handler.assert_not_awaited()
    assert json.loads(result.content)["error_code"] == "repair_cap_exceeded"


# ---------------------------------------------------------------------------
# Only invalid_tool_args counts
# ---------------------------------------------------------------------------


async def test_unrelated_tool_errors_do_not_count_toward_the_cap():
    """A blocked/auth_required/unparseable error is not a repairable-argument failure."""
    unrelated = ToolMessage(
        content=json.dumps({"error": "Token expired", "error_code": "auth_required"}),
        tool_call_id="tc1",
        name="render_surface",
        status="error",
    )
    prose = ToolMessage(content="just prose", tool_call_id="tc1", status="error")

    mw = make_repair_cap_middleware()
    handler = _handler(*(unrelated for _ in range(MAX_ATTEMPTS)), prose, _invalid_args_message())

    for _ in range(MAX_ATTEMPTS + 2):
        await mw.awrap_tool_call(_request("render_surface"), handler)

    assert handler.await_count == MAX_ATTEMPTS + 2

    # Only ONE invalid_tool_args failure was recorded, so the tool is still callable.
    handler.reset_mock()
    handler.side_effect = [_ok_message()]
    result = await mw.awrap_tool_call(_request("render_surface"), handler)
    handler.assert_awaited_once()
    assert result.status == "success"


# ---------------------------------------------------------------------------
# Built-ins fall through and are never counted
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("builtin_name", sorted(DEEPAGENTS_BUILTIN_NAMES))
async def test_builtins_fall_through_and_are_never_capped(builtin_name):
    """Every deepagents built-in passes through, however its result looks."""
    assert DEEPAGENTS_BUILTIN_NAMES, "built-in set must not be empty"
    mw = make_repair_cap_middleware()
    handler = _handler(*(_invalid_args_message(name=builtin_name) for _ in range(MAX_ATTEMPTS + 1)))

    for _ in range(MAX_ATTEMPTS + 1):
        result = await mw.awrap_tool_call(_request(builtin_name), handler)
        assert json.loads(result.content)["error_code"] == "invalid_tool_args"

    assert handler.await_count == MAX_ATTEMPTS + 1


# ---------------------------------------------------------------------------
# GraphInterrupt propagates
# ---------------------------------------------------------------------------


async def test_graph_interrupt_propagates_through_the_cap():
    """A handler raising GraphInterrupt must not be swallowed — the gates depend on it."""
    mw = make_repair_cap_middleware()
    handler = _handler()
    handler.side_effect = GraphInterrupt(())

    with pytest.raises(GraphInterrupt):
        await mw.awrap_tool_call(_request("send_email"), handler)

    handler.assert_awaited_once()
