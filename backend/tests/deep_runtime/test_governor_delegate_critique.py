"""Step 7B2 P5: the NET-NEW Governor LLM delegate-summary critique middleware.

The critique is the ONE lead-side ``@wrap_tool_call`` that does NOT skip the built-in
``task`` tool: it runs the read-only research delegate (the inner handler → a ``task``
``Command``), side-calls Haiku to critique the delegate's returned summary, and annotates
the summary with the verdict.

Two flavours of test:

* Unit tests drive the interceptor DIRECTLY via ``mw.awrap_tool_call(request, handler)``
  (same offline pattern as ``test_governor_audit.py``), with a FAKE critique client whose
  ``messages.create`` returns a scripted JSON verdict (or raises). No live API, no DB.

* Two wiring tests build a REAL ``AgentInvoker`` and assert ``_build_deep_agent_for``
  PREPENDS the critique to ``extra_middleware`` when ``deep_delegates_enabled`` and leaves
  the 7B1 5-tuple UNCHANGED when the flag is off (dormancy proof).

Invariant proven: a READ delegate is NEVER blocked (fail-open annotation); the WRITE branch
(unreached in production — 7B2 delegates are read-only) is REAL and BLOCKS a failed critique.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from langchain_core.messages import ToolMessage
from langgraph.types import Command

from src.deep_runtime.middleware.governor_delegate_critique import (
    make_governor_delegate_critique_middleware,
)

INVOKER = "src.orchestrator.agent_invoker"


# ── shared offline doubles ───────────────────────────────────────────────────


def _request(tool_name: str = "task", call_id: str = "tc1"):
    """Minimal ToolCallRequest stand-in: only ``.tool_call`` is read."""
    return SimpleNamespace(tool_call={"name": tool_name, "args": {}, "id": call_id})


def _hook(mw):
    """Extract the async wrap-tool-call hook bound on the middleware instance."""
    return mw.awrap_tool_call


def _summary_command(summary: str, *, call_id: str = "tc1", name: str = "task"):
    """A ``task`` Command carrying the delegate's summary ToolMessage (as ``task`` returns)."""
    tm = ToolMessage(content=summary, tool_call_id=call_id, name=name, status="success")
    return Command(update={"messages": [tm]})


def _handler_returning(value):
    """An async inner-handler stand-in that returns a fixed value (the delegate's result)."""

    async def handler(request):  # noqa: ANN001, ARG001
        return value

    return handler


def _fake_client(*, ok: bool = True, concerns: list[str] | None = None, raises: bool = False):
    """A fake Anthropic client whose ``messages.create`` returns a scripted JSON verdict."""
    client = MagicMock()
    if raises:
        client.messages.create = AsyncMock(side_effect=RuntimeError("critique model down"))
    else:
        payload = json.dumps({"ok": ok, "concerns": concerns or []})
        resp = SimpleNamespace(content=[SimpleNamespace(text=payload)])
        client.messages.create = AsyncMock(return_value=resp)
    return client


# ── (a) clean summary → read delegate → unreviewed=false, summary preserved ──


async def test_clean_summary_read_delegate_annotates_unreviewed_false():
    client = _fake_client(ok=True, concerns=[])
    mw = make_governor_delegate_critique_middleware(
        client=client, redis=None, is_read_only_delegate=True, model="haiku-test"
    )
    cmd = _summary_command(json.dumps({"findings": ["f1"], "synthesis": "s"}))

    result = await _hook(mw)(_request("task"), _handler_returning(cmd))

    assert isinstance(result, Command)
    tm = result.update["messages"][0]
    assert isinstance(tm, ToolMessage)
    assert tm.status == "success"  # a read is never blocked
    payload = json.loads(tm.content)
    assert payload["unreviewed"] is False
    assert payload["critique"]["ok"] is True
    assert payload["findings"] == ["f1"]  # delegate summary preserved
    assert payload["synthesis"] == "s"


# ── (b) flagged summary + read → unreviewed=true + concerns, STILL returned ───


async def test_flagged_summary_read_delegate_fails_open_annotated():
    client = _fake_client(ok=False, concerns=["hallucinated metric"])
    mw = make_governor_delegate_critique_middleware(
        client=client, redis=None, is_read_only_delegate=True, model="haiku-test"
    )
    cmd = _summary_command(json.dumps({"findings": ["f"]}))

    result = await _hook(mw)(_request("task"), _handler_returning(cmd))

    assert isinstance(result, Command)
    tm = result.update["messages"][0]
    assert tm.status == "success"  # fail-open: a flagged READ is NOT blocked
    payload = json.loads(tm.content)
    assert payload["unreviewed"] is True
    assert payload["critique"]["ok"] is False
    assert "hallucinated metric" in payload["critique"]["concerns"]
    assert payload["findings"] == ["f"]  # summary still delivered


# ── (c) critique model EXCEPTION + read → fail-open-annotated, NEVER blocked ──


async def test_critique_exception_read_delegate_fails_open():
    client = _fake_client(raises=True)
    mw = make_governor_delegate_critique_middleware(
        client=client, redis=None, is_read_only_delegate=True, model="haiku-test"
    )
    cmd = _summary_command(json.dumps({"findings": ["f"]}))

    result = await _hook(mw)(_request("task"), _handler_returning(cmd))

    assert isinstance(result, Command)
    tm = result.update["messages"][0]
    assert tm.status == "success"  # model outage NEVER blocks a read
    payload = json.loads(tm.content)
    assert payload["unreviewed"] is True
    assert payload["critique"]["concerns"] == ["critique unavailable"]
    assert payload["findings"] == ["f"]


# ── (c2) non-JSON summary content is wrapped, not lost ────────────────────────


async def test_non_json_summary_wrapped_and_annotated():
    client = _fake_client(ok=True, concerns=[])
    mw = make_governor_delegate_critique_middleware(
        client=client, redis=None, is_read_only_delegate=True, model="haiku-test"
    )
    cmd = _summary_command("just plain prose, not json")

    result = await _hook(mw)(_request("task"), _handler_returning(cmd))

    tm = result.update["messages"][0]
    payload = json.loads(tm.content)
    assert payload["summary"] == "just plain prose, not json"  # wrapped, not dropped
    assert payload["unreviewed"] is False


# ── (d) name != "task" → passthrough UNCHANGED; critique client NEVER called ──


async def test_non_task_tool_passthrough_unchanged():
    client = _fake_client(ok=True, concerns=[])
    mw = make_governor_delegate_critique_middleware(
        client=client, redis=None, is_read_only_delegate=True, model="haiku-test"
    )
    sentinel = ToolMessage(content="real-tool-output", tool_call_id="tc9", name="internal_search")

    async def handler(request):  # noqa: ANN001, ARG001
        return sentinel

    result = await _hook(mw)(_request("internal_search", "tc9"), handler)

    assert result is sentinel  # unchanged — the real gate downstream is NOT skipped
    client.messages.create.assert_not_awaited()  # no critique for a non-task tool


# ── (d2) non-Command / non-ToolMessage result → returned as-is ───────────────


async def test_task_non_command_result_returned_unchanged():
    client = _fake_client(ok=True, concerns=[])
    mw = make_governor_delegate_critique_middleware(
        client=client, redis=None, is_read_only_delegate=True, model="haiku-test"
    )
    plain = ToolMessage(content="not a command", tool_call_id="tc1", name="task")

    result = await _hook(mw)(_request("task"), _handler_returning(plain))

    assert result is plain
    client.messages.create.assert_not_awaited()


# ── (e) NEGATIVE CONTROL: write delegate + flagged critique → BLOCKED ─────────


async def test_negative_control_write_delegate_blocks_flagged_summary():
    """The read/write branch is REAL: with ``is_read_only_delegate=False`` a flagged critique
    BLOCKS (status='error'); the SAME flagged verdict on the READ path does NOT block. The two
    differ ONLY by the flag — that is the teeth proving the write branch is not dead code."""
    write_mw = make_governor_delegate_critique_middleware(
        client=_fake_client(ok=False, concerns=["overreach"]),
        redis=None,
        is_read_only_delegate=False,
        model="haiku-test",
    )
    write_result = await _hook(write_mw)(
        _request("task"), _handler_returning(_summary_command(json.dumps({"findings": ["f"]})))
    )

    # WRITE + flagged → a BLOCKED ToolMessage (status='error'), NOT an annotated Command.
    assert isinstance(write_result, ToolMessage)
    assert write_result.status == "error"
    wpayload = json.loads(write_result.content)
    assert wpayload["error"] == "delegate summary failed critique"
    assert "overreach" in wpayload["concerns"]

    # SAME flagged verdict on the READ path → NOT blocked (fail-open annotation).
    read_mw = make_governor_delegate_critique_middleware(
        client=_fake_client(ok=False, concerns=["overreach"]),
        redis=None,
        is_read_only_delegate=True,
        model="haiku-test",
    )
    read_result = await _hook(read_mw)(
        _request("task"), _handler_returning(_summary_command(json.dumps({"findings": ["f"]})))
    )
    assert isinstance(read_result, Command)
    read_tm = read_result.update["messages"][0]
    assert read_tm.status == "success"  # read with the SAME verdict is NOT blocked
    assert json.loads(read_tm.content)["unreviewed"] is True


# ── (e2) write delegate + clean critique → allowed (not a blanket block) ──────


async def test_write_delegate_clean_summary_allowed():
    client = _fake_client(ok=True, concerns=[])
    mw = make_governor_delegate_critique_middleware(
        client=client, redis=None, is_read_only_delegate=False, model="haiku-test"
    )
    cmd = _summary_command(json.dumps({"findings": ["f"]}))

    result = await _hook(mw)(_request("task"), _handler_returning(cmd))

    assert isinstance(result, Command)  # clean write is allowed through, annotated
    tm = result.update["messages"][0]
    assert tm.status == "success"
    payload = json.loads(tm.content)
    assert payload["unreviewed"] is False
    assert payload["findings"] == ["f"]


# ── (e3) write delegate + critique EXCEPTION → fail-CLOSED (blocked) ──────────


async def test_write_delegate_critique_exception_fails_closed():
    client = _fake_client(raises=True)
    mw = make_governor_delegate_critique_middleware(
        client=client, redis=None, is_read_only_delegate=False, model="haiku-test"
    )
    cmd = _summary_command(json.dumps({"findings": ["f"]}))

    result = await _hook(mw)(_request("task"), _handler_returning(cmd))

    assert isinstance(result, ToolMessage)
    assert result.status == "error"  # write fails CLOSED on critique outage
    assert json.loads(result.content)["error"] == "delegate summary failed critique"


# ── (f) redis cache: a second identical summary is served from cache ──────────


class _FakeRedis:
    def __init__(self):
        self.store: dict[str, str] = {}

    async def get(self, key):
        return self.store.get(key)

    async def setex(self, key, ttl, value):  # noqa: ANN001, ARG002
        self.store[key] = value


async def test_redis_cache_second_identical_summary_hits_cache():
    client = _fake_client(ok=True, concerns=[])
    redis = _FakeRedis()
    mw = make_governor_delegate_critique_middleware(
        client=client, redis=redis, is_read_only_delegate=True, model="haiku-test"
    )
    summary = json.dumps({"findings": ["f"]})

    r1 = await _hook(mw)(_request("task"), _handler_returning(_summary_command(summary)))
    r2 = await _hook(mw)(_request("task"), _handler_returning(_summary_command(summary)))

    # The LLM is consulted ONCE; the second identical summary is served from the redis cache.
    assert client.messages.create.await_count == 1
    assert json.loads(r1.update["messages"][0].content)["unreviewed"] is False
    assert json.loads(r2.update["messages"][0].content)["unreviewed"] is False


async def test_redis_failure_still_runs_critique():
    """A raising redis is best-effort: the critique still runs (fail-open on cache errors)."""
    bad_redis = MagicMock()
    bad_redis.get = AsyncMock(side_effect=RuntimeError("redis down"))
    bad_redis.setex = AsyncMock(side_effect=RuntimeError("redis down"))
    client = _fake_client(ok=True, concerns=[])
    mw = make_governor_delegate_critique_middleware(
        client=client, redis=bad_redis, is_read_only_delegate=True, model="haiku-test"
    )
    cmd = _summary_command(json.dumps({"findings": ["f"]}))

    result = await _hook(mw)(_request("task"), _handler_returning(cmd))

    assert isinstance(result, Command)
    assert json.loads(result.update["messages"][0].content)["unreviewed"] is False
    client.messages.create.assert_awaited_once()  # cache error did not suppress the critique


# ═══════════════════════════════════════════════════════════════════════════════
# Wiring: _build_deep_agent_for PREPENDS the critique when the flag is on and leaves
# the 7B1 5-tuple UNCHANGED when it is off (dormancy proof).
# ═══════════════════════════════════════════════════════════════════════════════


def _fake_db_factory():
    @asynccontextmanager
    async def _factory():
        yield SimpleNamespace(name="fake-db")

    return _factory


def _make_invoker(*, deep_delegates_enabled: bool):
    from src.orchestrator.agent_invoker import AgentInvoker
    from src.orchestrator.agents import SubAgent
    from tests.conftest import make_mock_settings

    tool_executor = MagicMock()

    async def fake_execute(name, args, uid, ws):  # noqa: ANN001, ARG001
        return {"ok": True}

    tool_executor.execute_tool = fake_execute

    agent = SubAgent(
        name="perceiver", prompt="p", model_tier="sonnet", capability_scope={"knowledge.search"}
    )

    return AgentInvoker(
        settings=make_mock_settings(runtime="deep", deep_delegates_enabled=deep_delegates_enabled),
        client=MagicMock(),
        services=None,  # → redis resolves to None via the extras-or-None guard
        budget=MagicMock(),
        circuit_breaker=MagicMock(),
        db_factory_provider=lambda: _fake_db_factory(),
        tool_executor=tool_executor,
        context=MagicMock(),
        agents={"perceiver": agent},
        checkpointer_provider=lambda: None,
    )


async def _call_build(invoker):
    agent = invoker._agents["perceiver"]
    return await invoker._build_deep_agent_for(
        agent,
        [],
        user_id="u",
        workspace_id="ws",
        thread_id="t1",
        authorization_source="direct_user_request",
        system_prompt="sys",
    )


async def test_wiring_flag_on_prepends_critique():
    invoker = _make_invoker(deep_delegates_enabled=True)
    sentinel = object()

    with (
        patch(f"{INVOKER}.build_deep_agent", new=AsyncMock(return_value=object())) as mock_build,
        patch(
            f"{INVOKER}.make_governor_delegate_critique_middleware", return_value=sentinel
        ) as mock_factory,
    ):
        await _call_build(invoker)

    mock_factory.assert_called_once()
    # redis sourced from services.extras (6C carry-fix pattern); services=None → None here.
    assert mock_factory.call_args.kwargs["redis"] is None
    assert mock_factory.call_args.kwargs["is_read_only_delegate"] is True
    assert mock_factory.call_args.kwargs["client"] is invoker._client

    mw_tuple = mock_build.call_args.kwargs["extra_middleware"]
    assert mw_tuple[0] is sentinel  # PREPENDED — outermost
    assert len(mw_tuple) == 6


async def test_wiring_flag_off_no_critique_five_tuple_unchanged():
    invoker = _make_invoker(deep_delegates_enabled=False)

    with (
        patch(f"{INVOKER}.build_deep_agent", new=AsyncMock(return_value=object())) as mock_build,
        patch(f"{INVOKER}.make_governor_delegate_critique_middleware") as mock_factory,
    ):
        await _call_build(invoker)

    mock_factory.assert_not_called()  # never built when the flag is off
    mw_tuple = mock_build.call_args.kwargs["extra_middleware"]
    assert len(mw_tuple) == 5  # the 7B1 5-tuple, UNCHANGED
