"""Step 7B2 P5: the NET-NEW Governor LLM delegate-summary critique middleware.

The critique is the ONE lead-side ``@wrap_tool_call`` that does NOT skip the built-in
``task`` tool: it runs the read-only research delegate (the inner handler → a ``task``
``Command``), side-calls Haiku (via the shared ``complete_text`` seam) to critique the
delegate's returned summary, and annotates the summary with the verdict.

Two flavours of test:

* Unit tests drive the interceptor DIRECTLY via ``mw.awrap_tool_call(request, handler)``
  (same offline pattern as ``test_governor_audit.py``), with ``complete_text`` patched to
  return a scripted JSON verdict (or raise). No live API, no DB.

* Two wiring tests build a REAL ``AgentInvoker`` and assert ``_build_deep_agent_for``
  PREPENDS the critique to ``extra_middleware`` when ``deep_delegates_enabled`` and leaves
  the base chain UNCHANGED (no critique prepend) when the flag is off (dormancy proof).

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
# The UtilityLLM seam imported into the critique module — patch target for the Haiku side-call.
_CT = "src.deep_runtime.middleware.governor_delegate_critique.complete_text"


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


def _complete_mock(*, ok: bool = True, concerns: list[str] | None = None, raises: bool = False):
    """AsyncMock standing in for ``complete_text`` — returns a scripted verdict JSON (or raises)."""
    if raises:
        return AsyncMock(side_effect=RuntimeError("critique model down"))
    return AsyncMock(return_value=json.dumps({"ok": ok, "concerns": concerns or []}))


# ── (a) clean summary → read delegate → unreviewed=false, summary preserved ──


async def test_clean_summary_read_delegate_annotates_unreviewed_false():
    ct = _complete_mock(ok=True, concerns=[])
    mw = make_governor_delegate_critique_middleware(redis=None, is_read_only_delegate=True)
    cmd = _summary_command(json.dumps({"findings": ["f1"], "synthesis": "s"}))

    with patch(_CT, ct):
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
    ct = _complete_mock(ok=False, concerns=["hallucinated metric"])
    mw = make_governor_delegate_critique_middleware(redis=None, is_read_only_delegate=True)
    cmd = _summary_command(json.dumps({"findings": ["f"]}))

    with patch(_CT, ct):
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
    ct = _complete_mock(raises=True)
    mw = make_governor_delegate_critique_middleware(redis=None, is_read_only_delegate=True)
    cmd = _summary_command(json.dumps({"findings": ["f"]}))

    with patch(_CT, ct):
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
    ct = _complete_mock(ok=True, concerns=[])
    mw = make_governor_delegate_critique_middleware(redis=None, is_read_only_delegate=True)
    cmd = _summary_command("just plain prose, not json")

    with patch(_CT, ct):
        result = await _hook(mw)(_request("task"), _handler_returning(cmd))

    tm = result.update["messages"][0]
    payload = json.loads(tm.content)
    assert payload["summary"] == "just plain prose, not json"  # wrapped, not dropped
    assert payload["unreviewed"] is False


# ── (c3) non-serializable content never raises in the read branch (default=str) ──


def test_annotate_content_non_serializable_does_not_raise():
    """``_annotate_content`` runs OUTSIDE any try/except in the read branch, so it must never
    raise on a non-serializable content — else a "never-blocks" read could error out. The real
    ``task`` always yields ``str`` content, so this is defense-in-depth (matches
    ``_safe_critique``'s ``json.dumps(..., default=str)``). Without ``default=str`` the final
    ``json.dumps`` raises ``TypeError`` and this test fails.
    """
    from src.deep_runtime.middleware.governor_delegate_critique import _annotate_content

    class _Weird:  # not JSON-serializable
        def __repr__(self) -> str:
            return "<weird-summary>"

    out = _annotate_content(
        _Weird(), unreviewed=True, critique_obj={"ok": False, "concerns": ["c"]}
    )
    payload = json.loads(out)  # valid JSON, did not raise
    assert payload["unreviewed"] is True
    assert payload["critique"]["ok"] is False
    assert payload["summary"] == "<weird-summary>"  # stringified via default=str, not dropped


# ── (d) name != "task" → passthrough UNCHANGED; critique seam NEVER called ──


async def test_non_task_tool_passthrough_unchanged():
    ct = _complete_mock(ok=True, concerns=[])
    mw = make_governor_delegate_critique_middleware(redis=None, is_read_only_delegate=True)
    sentinel = ToolMessage(content="real-tool-output", tool_call_id="tc9", name="internal_search")

    async def handler(request):  # noqa: ANN001, ARG001
        return sentinel

    with patch(_CT, ct):
        result = await _hook(mw)(_request("internal_search", "tc9"), handler)

    assert result is sentinel  # unchanged — the real gate downstream is NOT skipped
    ct.assert_not_awaited()  # no critique for a non-task tool


# ── (d2) non-Command / non-ToolMessage result → returned as-is ───────────────


async def test_task_non_command_result_returned_unchanged():
    ct = _complete_mock(ok=True, concerns=[])
    mw = make_governor_delegate_critique_middleware(redis=None, is_read_only_delegate=True)
    plain = ToolMessage(content="not a command", tool_call_id="tc1", name="task")

    with patch(_CT, ct):
        result = await _hook(mw)(_request("task"), _handler_returning(plain))

    assert result is plain
    ct.assert_not_awaited()


# ── (e) NEGATIVE CONTROL: write delegate + flagged critique → BLOCKED ─────────


async def test_negative_control_write_delegate_blocks_flagged_summary():
    """The read/write branch is REAL: with ``is_read_only_delegate=False`` a flagged critique
    BLOCKS (status='error'); the SAME flagged verdict on the READ path does NOT block. The two
    differ ONLY by the flag — that is the teeth proving the write branch is not dead code."""
    write_mw = make_governor_delegate_critique_middleware(redis=None, is_read_only_delegate=False)
    with patch(_CT, _complete_mock(ok=False, concerns=["overreach"])):
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
    read_mw = make_governor_delegate_critique_middleware(redis=None, is_read_only_delegate=True)
    with patch(_CT, _complete_mock(ok=False, concerns=["overreach"])):
        read_result = await _hook(read_mw)(
            _request("task"), _handler_returning(_summary_command(json.dumps({"findings": ["f"]})))
        )
    assert isinstance(read_result, Command)
    read_tm = read_result.update["messages"][0]
    assert read_tm.status == "success"  # read with the SAME verdict is NOT blocked
    assert json.loads(read_tm.content)["unreviewed"] is True


# ── (e2) write delegate + clean critique → allowed (not a blanket block) ──────


async def test_write_delegate_clean_summary_allowed():
    ct = _complete_mock(ok=True, concerns=[])
    mw = make_governor_delegate_critique_middleware(redis=None, is_read_only_delegate=False)
    cmd = _summary_command(json.dumps({"findings": ["f"]}))

    with patch(_CT, ct):
        result = await _hook(mw)(_request("task"), _handler_returning(cmd))

    assert isinstance(result, Command)  # clean write is allowed through, annotated
    tm = result.update["messages"][0]
    assert tm.status == "success"
    payload = json.loads(tm.content)
    assert payload["unreviewed"] is False
    assert payload["findings"] == ["f"]


# ── (e3) write delegate + critique EXCEPTION → fail-CLOSED (blocked) ──────────


async def test_write_delegate_critique_exception_fails_closed():
    ct = _complete_mock(raises=True)
    mw = make_governor_delegate_critique_middleware(redis=None, is_read_only_delegate=False)
    cmd = _summary_command(json.dumps({"findings": ["f"]}))

    with patch(_CT, ct):
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
    ct = _complete_mock(ok=True, concerns=[])
    redis = _FakeRedis()
    mw = make_governor_delegate_critique_middleware(redis=redis, is_read_only_delegate=True)
    summary = json.dumps({"findings": ["f"]})

    with patch(_CT, ct):
        r1 = await _hook(mw)(_request("task"), _handler_returning(_summary_command(summary)))
        r2 = await _hook(mw)(_request("task"), _handler_returning(_summary_command(summary)))

    # The LLM is consulted ONCE; the second identical summary is served from the redis cache.
    assert ct.await_count == 1
    assert json.loads(r1.update["messages"][0].content)["unreviewed"] is False
    assert json.loads(r2.update["messages"][0].content)["unreviewed"] is False


async def test_redis_failure_still_runs_critique():
    """A raising redis is best-effort: the critique still runs (fail-open on cache errors)."""
    bad_redis = MagicMock()
    bad_redis.get = AsyncMock(side_effect=RuntimeError("redis down"))
    bad_redis.setex = AsyncMock(side_effect=RuntimeError("redis down"))
    ct = _complete_mock(ok=True, concerns=[])
    mw = make_governor_delegate_critique_middleware(redis=bad_redis, is_read_only_delegate=True)
    cmd = _summary_command(json.dumps({"findings": ["f"]}))

    with patch(_CT, ct):
        result = await _hook(mw)(_request("task"), _handler_returning(cmd))

    assert isinstance(result, Command)
    assert json.loads(result.update["messages"][0].content)["unreviewed"] is False
    ct.assert_awaited_once()  # cache error did not suppress the critique


# ═══════════════════════════════════════════════════════════════════════════════
# Wiring: _build_deep_agent_for PREPENDS the critique when the flag is on and leaves
# the base chain UNCHANGED (no critique prepend) when it is off (dormancy proof).
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
        presence="absent",
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

    mw_tuple = mock_build.call_args.kwargs["extra_middleware"]
    assert mw_tuple[0] is sentinel  # PREPENDED — outermost
    # R3a: base chain is 8 (governor, unavailable, gate, write_lock, repair_cap, dispatcher,
    # librarian, budget); critique prepend → 9.
    assert len(mw_tuple) == 9


async def test_wiring_flag_off_no_critique_prepend():
    invoker = _make_invoker(deep_delegates_enabled=False)

    with (
        patch(f"{INVOKER}.build_deep_agent", new=AsyncMock(return_value=object())) as mock_build,
        patch(f"{INVOKER}.make_governor_delegate_critique_middleware") as mock_factory,
    ):
        await _call_build(invoker)

    mock_factory.assert_not_called()  # never built when the flag is off
    mw_tuple = mock_build.call_args.kwargs["extra_middleware"]
    # R3a base chain: governor, unavailable, gate, write_lock, repair_cap, dispatcher,
    # librarian, budget.
    assert len(mw_tuple) == 8  # no critique prepend when the delegates flag is off
