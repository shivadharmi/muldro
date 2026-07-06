"""Step 6B Task 4: AgentInvoker.resume_deep_turn re-enters a paused deep turn via
Command(resume=decision) on the Approval's stored thread_id.

Mirrors the ``_make_invoker`` pattern from test_agent_invoker_deep_hardening.py, but
overrides the db_factory so ``self._db_factory()`` yields a fake session whose
``.get(Approval, approval_id)`` returns a fake Approval carrying
``artifact_refs={"thread_id": ..., "agent_name": "perceiver"}``.

``build_deep_agent``, ``build_tool_shells``, ``make_jarvis_tool_dispatcher``, and
``stream_deep_agent_events`` are all patched — no real LangGraph runtime, no live API.
"""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from langgraph.types import Command

from src.orchestrator.agent_invoker import AgentInvoker
from src.orchestrator.agents import SubAgent
from tests.conftest import make_mock_settings


def _fake_approval(thread_id="t-resume", agent_name="perceiver"):
    return SimpleNamespace(
        artifact_refs={"thread_id": thread_id, "agent_name": agent_name},
        status="pending",
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
    approval = _fake_approval(thread_id="t-resume")
    inv, fake_db = _make_invoker_with_approval(approval)
    recorded: dict = {}

    with (
        patch("src.orchestrator.agent_invoker.build_deep_agent", new=AsyncMock()),
        patch("src.orchestrator.agent_invoker.build_tool_shells", return_value=["SHELL"]),
        patch("src.orchestrator.agent_invoker.make_jarvis_tool_dispatcher", return_value=object()),
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
    assert config["configurable"]["thread_id"] == "t-resume"

    assert recorded["kwargs"]["durability"] == "sync"

    assert approval.status == "approved"
    fake_db.commit.assert_awaited()


async def test_resume_reject_marks_rejected():
    approval = _fake_approval(thread_id="t-resume-2")
    inv, fake_db = _make_invoker_with_approval(approval)
    recorded: dict = {}

    with (
        patch("src.orchestrator.agent_invoker.build_deep_agent", new=AsyncMock()),
        patch("src.orchestrator.agent_invoker.build_tool_shells", return_value=["SHELL"]),
        patch("src.orchestrator.agent_invoker.make_jarvis_tool_dispatcher", return_value=object()),
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
