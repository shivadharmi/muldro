"""Step 10D P2.4c: the SSE resume endpoint ``POST /v1/muldro/chat/resume`` + the shared
``_stream_and_persist_chat`` fold/persist helper.

Three layers:

* **helper fold + SSE** — ``_stream_and_persist_chat`` serializes a typed CoreEvent stream to
  SSE frames (message_id + the per-event frames); with ``conversation_id=None`` the persist
  ``finally`` is skipped (no DB needed).
* **helper persist** (real DB) — with a conversation, the assistant reply (from a
  ``Presentation``) is persisted as a Message and the conversation aggregates bump.
* **endpoint wiring** — ``chat_resume`` drives ``orchestrator.resume_message_events`` and
  returns a StreamingResponse whose body carries the continuation frames.

The helper was previously exercised only by the (excluded) e2e chat test; these give it unit
coverage. Real-DB test skips when Postgres is unreachable.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from ulid import ULID

from src.api.routes_chat import ChatResumeRequest, _stream_and_persist_chat, chat_resume
from src.config.settings import get_settings
from src.orchestrator.core_events import (
    AgentDone,
    AgentStarted,
    Presentation,
    RunCompleted,
    TraceStarted,
)
from tests.conftest import make_mock_settings

pytestmark = pytest.mark.asyncio


def _mock_request():
    """A Request whose ``is_disconnected`` is an always-False AsyncMock."""
    request = MagicMock()
    request.is_disconnected = AsyncMock(return_value=False)
    return request


async def _agen(events):
    for e in events:
        yield e


def _parse_sse(frames: list[str]) -> list[dict]:
    """Parse ``event: X\\ndata: {...}\\n\\n`` frames into their JSON data dicts."""
    out = []
    for frame in frames:
        for line in frame.splitlines():
            if line.startswith("data: "):
                out.append(json.loads(line[len("data: ") :]))
    return out


# ── helper: fold + SSE serialization (no DB — conversation_id=None) ───────────────


async def test_helper_streams_message_id_and_response_and_done_frames():
    events = [
        TraceStarted(trace_id="trace_r"),
        AgentStarted(agent="lead", model="m"),
        AgentDone(agent="lead", text="All done."),
        Presentation(text="All done."),
        RunCompleted(trace_id="trace_r", run_id=None, surface_id=None),
    ]
    frames = [
        f
        async for f in _stream_and_persist_chat(
            _agen(events),
            request=_mock_request(),
            user_id="usr_1",
            conversation_id=None,  # skips the persist finally — no DB touched
            workspace_id="ws_1",
            surface="web",
            assistant_message_id="msg_resume_1",
        )
    ]
    data = _parse_sse(frames)
    kinds = [d.get("event") for d in data]
    # The leading message_id frame lets the frontend reuse/suppress the id (single bubble).
    assert {"event": "message_id", "message_id": "msg_resume_1"} in data
    # The re-homed reply surfaces as the `response` frame routes persist on.
    assert {"event": "response", "text": "All done."} in data
    # Terminal `done` frame closes the stream.
    assert "done" in kinds
    # No conversation frame when conversation_id is None.
    assert "conversation" not in kinds


async def test_helper_emits_client_safe_error_frame_on_raise():
    """A mid-stream raise becomes the sanitized `error` envelope (no raw exception leak)."""

    async def _boom():
        yield TraceStarted(trace_id="t")
        raise ValueError("connection refused to SECRET_HOST")

    frames = [
        f
        async for f in _stream_and_persist_chat(
            _boom(),
            request=_mock_request(),
            user_id="usr_1",
            conversation_id=None,
            workspace_id="ws_1",
            surface="web",
            assistant_message_id="msg_err",
        )
    ]
    data = _parse_sse(frames)
    err = [d for d in data if d.get("event") == "error"]
    assert err, f"expected an error frame, got {data}"
    assert "SECRET_HOST" not in json.dumps(err)
    assert "connection refused" not in json.dumps(err)


# ── endpoint wiring: chat_resume drives resume_message_events ─────────────────────


async def test_chat_resume_endpoint_drives_resume_and_streams_frames():
    events = [
        TraceStarted(trace_id="trace_e"),
        Presentation(text="Sent."),
        RunCompleted(trace_id="trace_e", run_id=None, surface_id=None),
    ]
    orch = MagicMock()
    # The facade returns an async generator (not a coroutine).
    orch.resume_message_events = MagicMock(return_value=_agen(events))

    req = ChatResumeRequest(approval_id="apr_1", decision="approve", reason=None)
    with patch("src.api.routes_chat._get_orchestrator", new=AsyncMock(return_value=orch)):
        resp = await chat_resume(
            req=req,
            request=_mock_request(),
            user_id="usr_1",
            workspace_id="ws_1",
            settings=make_mock_settings(),
        )
        body = [chunk async for chunk in resp.body_iterator]

    # The endpoint forwarded the decision/ids verbatim to the invoker chain.
    assert orch.resume_message_events.call_args.kwargs == {
        "approval_id": "apr_1",
        "decision": "approve",
        "reason": None,
        "user_id": "usr_1",
        "workspace_id": "ws_1",
        "conversation_id": None,
    }
    data = _parse_sse([c if isinstance(c, str) else c.decode() for c in body])
    assert {"event": "response", "text": "Sent."} in data
    assert "done" in [d.get("event") for d in data]


async def test_chat_resume_request_decision_is_constrained():
    """An out-of-taxonomy decision 422s loudly (Literal) rather than silently mis-routing."""
    import pydantic

    ChatResumeRequest(approval_id="a", decision="approve")
    ChatResumeRequest(approval_id="a", decision="reject")
    with pytest.raises(pydantic.ValidationError):
        ChatResumeRequest(approval_id="a", decision="maybe")


# ── helper persist (real DB) ─────────────────────────────────────────────────────


def _db_reachable() -> bool:
    import asyncpg

    dsn = get_settings().database_url.replace("+asyncpg", "", 1)

    async def _probe() -> None:
        conn = await asyncpg.connect(dsn=dsn)
        try:
            await conn.execute("SELECT 1")
        finally:
            await conn.close()

    try:
        asyncio.run(_probe())
        return True
    except Exception:  # pragma: no cover - environment-dependent
        return False


_DB_UP = _db_reachable()
_db_skip = pytest.mark.skipif(not _DB_UP, reason="Postgres not reachable")


@asynccontextmanager
async def _conversation_env():
    """Seed User → Workspace → Conversation; yield ``(factory, ids...)``; clean up."""
    from src.models.conversations import Conversation, Message
    from src.models.users import User, Workspace

    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    suffix = str(ULID())
    user_id = f"usr_{suffix}"
    workspace_id = f"ws_{suffix}"
    conversation_id = f"conv_{suffix}"
    try:
        async with factory() as db:
            db.add(
                User(
                    user_id=user_id,
                    email=f"resume-persist-{suffix}@example.com",
                    display_name="resume-persist",
                )
            )
            db.add(Workspace(workspace_id=workspace_id, name="resume-ws", owner_user_id=user_id))
            await db.commit()
            db.add(
                Conversation(
                    conversation_id=conversation_id,
                    user_id=user_id,
                    workspace_id=workspace_id,
                    surface="web",
                    status="active",
                )
            )
            await db.commit()
        yield factory, user_id, workspace_id, conversation_id
    finally:
        try:
            async with factory() as db:
                await db.execute(delete(Message).where(Message.workspace_id == workspace_id))
                await db.execute(
                    delete(Conversation).where(Conversation.workspace_id == workspace_id)
                )
                await db.execute(delete(Workspace).where(Workspace.workspace_id == workspace_id))
                await db.execute(delete(User).where(User.user_id == user_id))
                await db.commit()
        except Exception:  # pragma: no cover - teardown best-effort
            pass
        await engine.dispose()


@_db_skip
async def test_helper_persists_assistant_reply_to_conversation():
    """With a conversation, the re-homed reply is persisted as an assistant Message so the
    resumed chat bubble is not empty (Corr-C1)."""
    from src.models.conversations import Message

    async with _conversation_env() as (factory, user_id, workspace_id, conversation_id):
        events = [
            TraceStarted(trace_id="trace_p"),
            Presentation(text="Email sent to Alex."),
            RunCompleted(trace_id="trace_p", run_id=None, surface_id=None),
        ]
        _ = [
            f
            async for f in _stream_and_persist_chat(
                _agen(events),
                request=_mock_request(),
                user_id=user_id,
                conversation_id=conversation_id,
                workspace_id=workspace_id,
                surface="web",
                assistant_message_id="msg_persist_1",
            )
        ]

        async with factory() as db:
            row = (
                await db.execute(select(Message).where(Message.message_id == "msg_persist_1"))
            ).scalar_one()
            assert row.role == "assistant"
            assert row.content == "Email sent to Alex."
            assert row.conversation_id == conversation_id
            assert row.trace_id == "trace_p"


@_db_skip
async def test_helper_counts_user_and_assistant_on_initial_turn():
    """The initial turn's user Message is inserted by ``chat_stream`` (not the helper) and NOT
    counted there, so the helper counts BOTH the user message and the assistant reply → +2.
    Pins the default so a regression can't silently drop the user-message count."""
    from src.models.conversations import Conversation

    async with _conversation_env() as (factory, user_id, workspace_id, conversation_id):
        async with factory() as db:
            before = (
                await db.execute(
                    select(Conversation.message_count).where(
                        Conversation.conversation_id == conversation_id
                    )
                )
            ).scalar_one()

        events = [
            TraceStarted(trace_id="trace_i"),
            Presentation(text="Done."),
            RunCompleted(trace_id="trace_i", run_id=None, surface_id=None),
        ]
        # The helper persists via the app-global get_session_factory() (thread-local,
        # loop-bound); under the per-test asyncio.run loop that engine cross-loop-fails after
        # the first real-DB test binds it. Point it at THIS test's current-loop factory (same
        # Postgres) so the persist is loop-safe and deterministic.
        with patch("src.models.database.get_session_factory", return_value=factory):
            _ = [
                f
                async for f in _stream_and_persist_chat(
                    _agen(events),
                    request=_mock_request(),
                    user_id=user_id,
                    conversation_id=conversation_id,
                    workspace_id=workspace_id,
                    surface="web",
                    assistant_message_id="msg_initial_1",
                )
            ]

        async with factory() as db:
            after = (
                await db.execute(
                    select(Conversation.message_count).where(
                        Conversation.conversation_id == conversation_id
                    )
                )
            ).scalar_one()
        assert after - before == 2


@_db_skip
async def test_helper_resume_counts_only_assistant_message():
    """F2 (Codex P2): on approval-resume, no new USER message is inserted (``chat_stream`` inserts
    it only on the initial turn), so the shared persist helper must bump ``message_count`` by
    exactly 1 (the assistant reply), not 2. Counting 2 reports a phantom user message per resume,
    compounding across chained approvals."""
    from src.models.conversations import Conversation

    async with _conversation_env() as (factory, user_id, workspace_id, conversation_id):
        async with factory() as db:
            before = (
                await db.execute(
                    select(Conversation.message_count).where(
                        Conversation.conversation_id == conversation_id
                    )
                )
            ).scalar_one()

        events = [
            TraceStarted(trace_id="trace_r"),
            Presentation(text="Booked."),
            RunCompleted(trace_id="trace_r", run_id=None, surface_id=None),
        ]
        with patch("src.models.database.get_session_factory", return_value=factory):
            _ = [
                f
                async for f in _stream_and_persist_chat(
                    _agen(events),
                    request=_mock_request(),
                    user_id=user_id,
                    conversation_id=conversation_id,
                    workspace_id=workspace_id,
                    surface="web",
                    assistant_message_id="msg_resume_1",
                    count_user_message=False,  # resume: no new user message was inserted
                )
            ]

        async with factory() as db:
            after = (
                await db.execute(
                    select(Conversation.message_count).where(
                        Conversation.conversation_id == conversation_id
                    )
                )
            ).scalar_one()
        assert after - before == 1


@_db_skip
async def test_helper_refuses_cross_tenant_conversation_persist():
    """SECURITY (P2.4 review, property F): a caller in a DIFFERENT workspace/user supplying a
    victim's ``conversation_id`` persists NOTHING — no injected Message, no aggregate bump."""
    from src.models.conversations import Conversation, Message

    async with _conversation_env() as (factory, victim_user, victim_ws, victim_conv):
        async with factory() as db:
            before = (
                await db.execute(
                    select(Conversation.message_count).where(
                        Conversation.conversation_id == victim_conv
                    )
                )
            ).scalar_one()

        events = [
            TraceStarted(trace_id="trace_x"),
            Presentation(text="INJECTED"),
            RunCompleted(trace_id="trace_x", run_id=None, surface_id=None),
        ]
        # Attacker: a different user + workspace, but the VICTIM's conversation_id.
        _ = [
            f
            async for f in _stream_and_persist_chat(
                _agen(events),
                request=_mock_request(),
                user_id="usr_attacker",
                conversation_id=victim_conv,
                workspace_id="ws_attacker",
                surface="web",
                assistant_message_id="msg_injected",
            )
        ]

        async with factory() as db:
            # No injected message landed in the victim's conversation.
            injected = (
                await db.execute(select(Message).where(Message.message_id == "msg_injected"))
            ).scalar_one_or_none()
            assert injected is None
            # The victim conversation's aggregates were NOT bumped cross-tenant.
            after = (
                await db.execute(
                    select(Conversation.message_count).where(
                        Conversation.conversation_id == victim_conv
                    )
                )
            ).scalar_one()
            assert after == before
