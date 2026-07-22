"""Unit tests for deep_runtime.middleware.budget.make_budget_middleware.

No live API/DB. The middleware is an ``@after_model`` hook (async, invoked via
``aafter_model``) that extracts usage from the last AI message and records it via
``budget.record_usage`` inside a ``db_factory`` context. A budget write must
NEVER raise into the agent loop — failures are swallowed and logged.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage

from src.deep_runtime.middleware.budget import make_budget_middleware

WORKSPACE_ID = "ws_test"


def _fake_db_factory() -> tuple:
    """Return (db_factory, db) where db_factory is an async context manager.

    The yielded ``db`` is an AsyncMock so ``await db.commit()`` works and is
    observable. ``db_factory`` mimics ``async with db_factory() as db:``.
    """
    db = AsyncMock()

    @asynccontextmanager
    async def factory():
        yield db

    return factory, db


def _state_with_usage(
    *, input_tokens: int = 100, output_tokens: int = 20, details: dict | None = None
) -> dict:
    msg = AIMessage(
        content="hello",
        usage_metadata={
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
            **({"input_token_details": details} if details else {}),
        },
    )
    return {"messages": [AIMessage(content="earlier"), msg]}


async def _run_hook(mw: AgentMiddleware, state: dict):
    """Invoke the middleware's async after_model hook with a dummy runtime."""
    result = await mw.aafter_model(state, MagicMock())
    return result


def test_factory_returns_agent_middleware():
    factory, _ = _fake_db_factory()
    mw = make_budget_middleware(
        agent_name="planner",
        model="claude-opus-4-8",
        workspace_id=WORKSPACE_ID,
        db_factory=factory,
        budget=AsyncMock(),
    )
    assert isinstance(mw, AgentMiddleware)


async def test_records_usage_with_correct_tokens_and_workspace():
    budget = AsyncMock()
    budget.record_usage = AsyncMock()
    factory, db = _fake_db_factory()

    mw = make_budget_middleware(
        agent_name="planner",
        model="claude-opus-4-8",
        workspace_id=WORKSPACE_ID,
        db_factory=factory,
        budget=budget,
        trace_id="trace_1",
        trigger="chat",
    )

    result = await _run_hook(mw, _state_with_usage(input_tokens=123, output_tokens=45))

    assert result is None  # pure side effect
    budget.record_usage.assert_awaited_once()
    _, kwargs = budget.record_usage.await_args
    assert kwargs["agent_name"] == "planner"
    assert kwargs["model"] == "claude-opus-4-8"
    assert kwargs["input_tokens"] == 123
    assert kwargs["output_tokens"] == 45
    assert kwargs["workspace_id"] == WORKSPACE_ID
    assert kwargs["trace_id"] == "trace_1"
    assert kwargs["trigger"] == "chat"
    # cache fields default to 0 when not present in usage_metadata
    assert kwargs["cache_creation_input_tokens"] == 0
    assert kwargs["cache_read_input_tokens"] == 0
    db.commit.assert_awaited_once()


async def test_records_cache_fields_when_present():
    budget = AsyncMock()
    budget.record_usage = AsyncMock()
    factory, _ = _fake_db_factory()

    mw = make_budget_middleware(
        agent_name="perceiver",
        model="claude-sonnet-4-6",
        workspace_id=WORKSPACE_ID,
        db_factory=factory,
        budget=budget,
    )

    await _run_hook(
        mw,
        _state_with_usage(details={"cache_read": 9, "cache_creation": 7}),
    )

    _, kwargs = budget.record_usage.await_args
    assert kwargs["cache_read_input_tokens"] == 9
    assert kwargs["cache_creation_input_tokens"] == 7


async def test_swallows_record_usage_error():
    budget = AsyncMock()
    budget.record_usage = AsyncMock(side_effect=ValueError("workspace_id is required"))
    factory, _ = _fake_db_factory()

    mw = make_budget_middleware(
        agent_name="planner",
        model="claude-opus-4-8",
        workspace_id=WORKSPACE_ID,
        db_factory=factory,
        budget=budget,
    )

    # Must NOT raise — a budget failure can never break the agent loop.
    result = await _run_hook(mw, _state_with_usage())
    assert result is None
    budget.record_usage.assert_awaited_once()


async def test_no_usage_metadata_does_not_crash():
    budget = AsyncMock()
    budget.record_usage = AsyncMock()
    factory, _ = _fake_db_factory()

    mw = make_budget_middleware(
        agent_name="planner",
        model="claude-opus-4-8",
        workspace_id=WORKSPACE_ID,
        db_factory=factory,
        budget=budget,
    )

    state = {"messages": [AIMessage(content="no usage here")]}  # usage_metadata is None
    result = await _run_hook(mw, state)

    assert result is None
    budget.record_usage.assert_not_awaited()


async def test_empty_messages_does_not_crash():
    budget = AsyncMock()
    budget.record_usage = AsyncMock()
    factory, _ = _fake_db_factory()

    mw = make_budget_middleware(
        agent_name="planner",
        model="claude-opus-4-8",
        workspace_id=WORKSPACE_ID,
        db_factory=factory,
        budget=budget,
    )

    result = await _run_hook(mw, {"messages": []})
    assert result is None
    budget.record_usage.assert_not_awaited()
