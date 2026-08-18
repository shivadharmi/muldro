"""Step 6B Task 4: AgentInvoker.resume_deep_turn re-enters a paused deep turn via
Command(resume=decision) on the Approval's stored thread_id.

Mirrors the ``_make_invoker`` pattern from test_agent_invoker_deep_hardening.py, but
overrides the db_factory so ``self._db_factory()`` yields a fake session whose
``.get(Approval, approval_id)`` returns a fake Approval carrying
``artifact_refs={"thread_id": ..., "agent_name": "perceiver"}``.

``build_deep_agent``, ``build_tool_shells``, ``make_muldro_tool_dispatcher``, and
``stream_deep_agent_events`` are all patched — no real LangGraph runtime, no live API.
"""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from langgraph.types import Command

from src.deep_runtime.thread_identity import make_thread_id
from src.orchestrator.agent_invoker import AgentInvoker
from src.orchestrator.agents import SubAgent
from tests.conftest import make_mock_settings


def _fake_approval(thread_id=None, agent_name="perceiver", workspace_id="ws", status="pending"):
    # A6 (Step-10A): default to a workspace-bound thread_id so the resume path's
    # cross-workspace guard round-trips (a colonless literal would now be refused).
    if thread_id is None:
        thread_id = make_thread_id(workspace_id)
    return SimpleNamespace(
        workspace_id=workspace_id,
        artifact_refs={"thread_id": thread_id, "agent_name": agent_name},
        status=status,
        decided_at=None,
        approved_by=None,
    )


def _make_invoker_with_approval(approval) -> AgentInvoker:
    """Build a real AgentInvoker whose db_factory yields a fake session.

    ``db.get(Approval, approval_id)`` resolves to *approval* (or None); ``db.commit`` is
    an AsyncMock so the resume path's commit can be asserted awaited.
    """
    tool_executor = MagicMock()
    tool_executor.apply_cache_control_to_tools = lambda tools: tools
    tool_executor.get_tools_for_agent = AsyncMock(return_value=[])

    context = MagicMock()
    context.assemble_context = AsyncMock(return_value="")

    agent = SubAgent(name="perceiver", prompt="p", model_tier="sonnet", capability_scope=set())

    fake_db = MagicMock(name="fake-db")
    fake_db.get = AsyncMock(return_value=approval)
    fake_db.commit = AsyncMock()
    # I1 atomic flip: resume_deep_turn consumes the pending approval via a conditional
    # UPDATE (``_cas_flip_pending``). rowcount=1 = THIS resume won the flip (happy path).
    fake_db.execute = AsyncMock(return_value=SimpleNamespace(rowcount=1))

    @asynccontextmanager
    async def _db_factory():
        yield fake_db

    inv = AgentInvoker(
        settings=make_mock_settings(runtime="deep"),
        client=MagicMock(),
        services=None,
        budget=MagicMock(),
        circuit_breaker=MagicMock(),
        db_factory_provider=lambda: _db_factory,
        tool_executor=tool_executor,
        context=context,
        agents={"perceiver": agent},
    )
    return inv, fake_db


def _fake_stream_recorder(recorded: dict):
    async def _fake_stream(*args, **kwargs):
        recorded["args"] = args
        recorded["kwargs"] = kwargs
        yield {
            "event": "agent_done",
            "agent": "perceiver",
            "text": "ok",
            "input_tokens": 1,
            "output_tokens": 1,
            "cache_creation_tokens": 0,
            "cache_read_tokens": 0,
            "tools_called": [],
            "latency_ms": 1,
            "cost_usd": 0.0,
        }

    return _fake_stream


async def test_resume_approve_calls_stream_with_command_and_marks_approved():
    approval = _fake_approval()
    inv, fake_db = _make_invoker_with_approval(approval)
    recorded: dict = {}

    with (
        patch("src.orchestrator.agent_invoker.build_deep_agent", new=AsyncMock()),
        patch("src.orchestrator.agent_invoker.build_tool_shells", return_value=["SHELL"]),
        patch("src.orchestrator.agent_invoker.make_muldro_tool_dispatcher", return_value=object()),
        patch(
            "src.orchestrator.agent_invoker.stream_deep_agent_events",
            _fake_stream_recorder(recorded),
        ),
    ):
        frames = [
            f
            async for f in inv.resume_deep_turn(
                approval_id="apr_x", decision="approve", user_id="u", workspace_id="ws"
            )
        ]

    assert any(f["event"] == "agent_done" for f in frames)

    graph_input = recorded["args"][1]
    assert isinstance(graph_input, Command)
    assert graph_input.resume == "approve"

    config = recorded["args"][2]
    assert config["configurable"]["thread_id"] == approval.artifact_refs["thread_id"]

    assert recorded["kwargs"]["durability"] == "sync"

    assert approval.status == "approved"
    fake_db.commit.assert_awaited()


async def test_resume_reject_marks_rejected():
    approval = _fake_approval()
    inv, fake_db = _make_invoker_with_approval(approval)
    recorded: dict = {}

    with (
        patch("src.orchestrator.agent_invoker.build_deep_agent", new=AsyncMock()),
        patch("src.orchestrator.agent_invoker.build_tool_shells", return_value=["SHELL"]),
        patch("src.orchestrator.agent_invoker.make_muldro_tool_dispatcher", return_value=object()),
        patch(
            "src.orchestrator.agent_invoker.stream_deep_agent_events",
            _fake_stream_recorder(recorded),
        ),
    ):
        frames = [
            f
            async for f in inv.resume_deep_turn(
                approval_id="apr_y", decision="reject", user_id="u", workspace_id="ws"
            )
        ]

    assert any(f["event"] == "agent_done" for f in frames)

    graph_input = recorded["args"][1]
    assert isinstance(graph_input, Command)
    assert graph_input.resume == "reject"

    assert approval.status == "rejected"
    fake_db.commit.assert_awaited()


async def test_resume_unknown_approval_yields_error_and_never_streams():
    inv, fake_db = _make_invoker_with_approval(None)

    with patch("src.orchestrator.agent_invoker.stream_deep_agent_events") as mock_stream:
        frames = [
            f
            async for f in inv.resume_deep_turn(
                approval_id="apr_missing", decision="approve", user_id="u", workspace_id="ws"
            )
        ]

    assert any(f["event"] == "error" for f in frames)
    mock_stream.assert_not_called()


async def test_resume_cross_tenant_approval_is_not_found_and_never_streams():
    """IDOR guard: an Approval owned by another workspace is unresumable and its state is
    NOT mutated; the response is the same generic "not found" so existence is not leaked."""
    approval = _fake_approval(workspace_id="ws_victim")
    inv, fake_db = _make_invoker_with_approval(approval)

    with patch("src.orchestrator.agent_invoker.stream_deep_agent_events") as mock_stream:
        frames = [
            f
            async for f in inv.resume_deep_turn(
                approval_id="apr_x",
                decision="approve",
                user_id="attacker",
                workspace_id="ws_attacker",
            )
        ]

    assert any(f["event"] == "error" and f.get("message") == "approval not found" for f in frames)
    mock_stream.assert_not_called()
    # the victim's approval was NEVER mutated
    assert approval.status == "pending"
    assert approval.approved_by is None
    fake_db.commit.assert_not_awaited()


async def test_resume_already_decided_approval_is_blocked():
    """Only a still-pending approval may be resumed — an already-decided one is blocked so
    the tool cannot be re-executed by a replayed/duplicate resume."""
    approval = _fake_approval(status="approved")
    inv, fake_db = _make_invoker_with_approval(approval)

    with patch("src.orchestrator.agent_invoker.stream_deep_agent_events") as mock_stream:
        frames = [
            f
            async for f in inv.resume_deep_turn(
                approval_id="apr_x", decision="approve", user_id="u", workspace_id="ws"
            )
        ]

    assert any(f["event"] == "error" and f.get("message") == "approval not pending" for f in frames)
    mock_stream.assert_not_called()
    fake_db.commit.assert_not_awaited()
