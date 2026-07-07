"""Step 6C CF-1: persist the assembled ContextPack on the Approval at pause time and
re-inject it when the deep turn resumes.

The deep-runtime approval gate pauses a turn via ``interrupt()`` and persists an
Approval. On resume, ``AgentInvoker.resume_deep_turn`` used to rebuild the agent with an
EMPTY context block (``build_system_prompt(agent, "")``), losing the original turn's
ambient context (entities/memories/preferences the ContextAssembler produced). The fix:

* the trust_gate persists ``context_block`` (capped at ``_MAX_PERSISTED_CONTEXT_CHARS``)
  into the Approval's ``artifact_refs`` at pause time; and
* ``resume_deep_turn`` reads it back and re-injects it via
  ``build_system_prompt(agent, persisted_context)``.

Two flavours of test:

* Persist-at-pause (pure unit): ``_decide_and_maybe_persist(..., context_block=...)`` is
  called directly with fakes (no DB, no LLM) — mirrors the doubles in
  ``tests/deep_runtime/test_trust_gate.py`` — asserting the captured
  ``artifact_refs["context_block"]`` equals the passed context and is truncated to the cap.

* Re-inject-on-resume (real DB, skips when Postgres is unreachable): a PENDING Approval is
  seeded whose ``artifact_refs`` carries a sentinel ``context_block``; ``resume_deep_turn``
  is driven to exhaustion with ``build_system_prompt`` spied — the captured ``context`` arg
  must be the sentinel (NOT "").
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from ulid import ULID

from src.config.settings import get_settings
from src.deep_runtime.middleware.trust_gate import (
    _MAX_PERSISTED_CONTEXT_CHARS,
    _decide_and_maybe_persist,
)
from src.models.approvals import Approval
from src.models.trust_state import TrustCeiling, TrustState
from src.models.users import User, Workspace
from src.orchestrator.agent_invoker import AgentInvoker
from src.orchestrator.agents import SubAgent
from src.services.risk_assessor import RiskAssessment
from tests.conftest import make_mock_settings

MODULE = "src.deep_runtime.middleware.trust_gate"
INVOKER_MODULE = "src.orchestrator.agent_invoker"
USER_ID = "u_test"
WORKSPACE_ID = "ws_test"
THREAD_ID = "chat_thread_1"


# ── shared test doubles (mirrors tests/deep_runtime/test_trust_gate.py) ──────────


def _persist_db_factory(existing=None):
    """A db_factory whose session backs the decide/persist block.

    ``.execute(...).scalars().first()`` resolves to *existing* (default ``None`` so the
    idempotent get-or-create takes the create branch); ``.commit`` is an AsyncMock.
    """

    @asynccontextmanager
    async def _factory():
        db = MagicMock(name="persist-db")
        result = MagicMock(name="execute-result")
        result.scalars.return_value.first.return_value = existing
        db.execute = AsyncMock(return_value=result)
        db.commit = AsyncMock()
        db.add = MagicMock()
        yield db

    return _factory


# ── Persist-at-pause (unit): context_block lands in artifact_refs, truncated ──────


async def test_persist_stores_context_block_on_approval():
    """A non-empty ``context_block`` is stored verbatim under
    ``artifact_refs["context_block"]`` when the gate persists a new Approval."""
    risk = RiskAssessment(
        risk_level="high", reasoning="x", reversible=False, blast_radius="external_single"
    )
    fake_te = MagicMock()
    fake_te.evaluate = AsyncMock(
        return_value=SimpleNamespace(decision="approval_required", justification="risky")
    )
    captured: dict = {}

    async def fake_create_approval(db, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(approval_id="apr_new")

    ctx = "ORIGINAL_TURN_CONTEXT_" + ("z" * 200)
    with (
        patch(f"{MODULE}.TrustEngine", return_value=fake_te),
        patch(f"{MODULE}.create_approval", side_effect=fake_create_approval),
    ):
        require_approval, approval_id = await _decide_and_maybe_persist(
            name="echo",
            capability="email.send",
            risk=risk,
            workspace_id=WORKSPACE_ID,
            user_id=USER_ID,
            thread_id=THREAD_ID,
            tool_call_id="call_echo",
            agent_name="executor",
            db_factory=_persist_db_factory(),
            context_block=ctx,
        )

    assert require_approval is True
    assert approval_id == "apr_new"
    assert captured["artifact_refs"]["context_block"] == ctx


async def test_persist_truncates_context_block_to_cap():
    """A context_block longer than the cap is truncated to
    ``_MAX_PERSISTED_CONTEXT_CHARS`` before being persisted (artifact_refs is JSONB but
    kept bounded)."""
    risk = RiskAssessment(
        risk_level="high", reasoning="x", reversible=False, blast_radius="external_single"
    )
    fake_te = MagicMock()
    fake_te.evaluate = AsyncMock(
        return_value=SimpleNamespace(decision="approval_required", justification="risky")
    )
    captured: dict = {}

    async def fake_create_approval(db, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(approval_id="apr_new")

    ctx = "A" * (_MAX_PERSISTED_CONTEXT_CHARS + 500)
    with (
        patch(f"{MODULE}.TrustEngine", return_value=fake_te),
        patch(f"{MODULE}.create_approval", side_effect=fake_create_approval),
    ):
        await _decide_and_maybe_persist(
            name="echo",
            capability="email.send",
            risk=risk,
            workspace_id=WORKSPACE_ID,
            user_id=USER_ID,
            thread_id=THREAD_ID,
            tool_call_id="call_echo",
            agent_name="executor",
            db_factory=_persist_db_factory(),
            context_block=ctx,
        )

    stored = captured["artifact_refs"]["context_block"]
    assert stored == ctx[:_MAX_PERSISTED_CONTEXT_CHARS]
    assert len(stored) == _MAX_PERSISTED_CONTEXT_CHARS


# ── Re-inject-on-resume (real DB) ────────────────────────────────────────────────


def _db_reachable() -> bool:
    """Best-effort raw connect to Postgres to decide whether to skip.

    Mirrors ``tests/test_deep_gate_durable_resume_db.py``: a raw asyncpg connect on its
    own throwaway loop, never touching the app's process-wide cached engine.
    """
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


_DB_REACHABLE = _db_reachable()


@asynccontextmanager
async def _gate_env():
    """Yield ``(factory, user_id, workspace_id)`` with the FK parents seeded.

    Teardown deletes Approvals + TrustStates + TrustCeilings for the workspace, then
    the Workspace + User, then disposes the engine — all on this test's own loop.
    """
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    suffix = str(ULID())
    user_id = f"usr_{suffix}"
    workspace_id = f"ws_{suffix}"
    try:
        async with factory() as db:
            db.add(
                User(
                    user_id=user_id,
                    email=f"cf1-{suffix}@example.com",
                    display_name="cf1-reinjection-test",
                )
            )
            db.add(Workspace(workspace_id=workspace_id, name="cf1-ws", owner_user_id=user_id))
            await db.commit()
        yield factory, user_id, workspace_id
    finally:
        try:
            async with factory() as db:
                await db.execute(delete(Approval).where(Approval.workspace_id == workspace_id))
                await db.execute(delete(TrustState).where(TrustState.workspace_id == workspace_id))
                await db.execute(
                    delete(TrustCeiling).where(TrustCeiling.workspace_id == workspace_id)
                )
                await db.execute(delete(Workspace).where(Workspace.workspace_id == workspace_id))
                await db.execute(delete(User).where(User.user_id == user_id))
                await db.commit()
        except Exception:  # pragma: no cover - teardown best-effort
            pass
        await engine.dispose()


def _make_invoker(*, factory) -> AgentInvoker:
    """A minimal real ``AgentInvoker`` exposing ``resume_deep_turn`` over a real DB.

    The rebuild machinery (``_build_deep_agent_for`` / ``stream_deep_agent_events``) is
    patched out by the test, so only ``db_factory_provider`` + ``agents`` matter here.
    """
    tool_executor = MagicMock()
    tool_executor.apply_cache_control_to_tools = lambda tools: tools
    tool_executor.get_tools_for_agent = AsyncMock(return_value=[])

    context = MagicMock()
    context.assemble_context = AsyncMock(return_value="")

    agent = SubAgent(
        name="executor", prompt="p", model_tier="sonnet", capability_scope={"email.send"}
    )

    return AgentInvoker(
        settings=make_mock_settings(runtime="deep"),
        client=MagicMock(),
        services=None,
        budget=MagicMock(),
        circuit_breaker=MagicMock(),
        db_factory_provider=lambda: factory,
        tool_executor=tool_executor,
        context=context,
        agents={"executor": agent},
        checkpointer_provider=lambda: MagicMock(),
    )


def _seed_pending_approval(*, approval_id: str, user_id: str, workspace_id: str, refs: dict):
    return Approval(
        approval_id=approval_id,
        user_id=user_id,
        workspace_id=workspace_id,
        execution_id=f"exec_{ULID()}",
        approval_type="tool:send_email",
        title="CF-1 re-injection approval",
        artifact_refs=refs,
        status="pending",
    )


@pytest.mark.skipif(not _DB_REACHABLE, reason="Postgres not reachable")
async def test_resume_reinjects_persisted_context_block():
    """A PENDING approval carrying a persisted ``context_block`` sentinel must, on resume,
    rebuild the agent's system prompt with that sentinel as the context — proving the
    original turn's ambient context survives the approval round-trip (NOT lost to "")."""
    sentinel = "ORIGINAL_TURN_CONTEXT_SENTINEL"

    async with _gate_env() as (factory, user_id, workspace_id):
        approval_id = f"apr_{ULID()}"
        thread_id = f"chat_{ULID()}"
        async with factory() as db:
            db.add(
                _seed_pending_approval(
                    approval_id=approval_id,
                    user_id=user_id,
                    workspace_id=workspace_id,
                    refs={
                        "thread_id": thread_id,
                        "agent_name": "executor",
                        "tool_call_id": "call_send_1",
                        "context_block": sentinel,
                    },
                )
            )
            await db.commit()

        invoker = _make_invoker(factory=factory)

        # Spy on build_system_prompt to capture the context arg the resume passes it,
        # while still returning the REAL prompt blocks (so build_system_message works).
        original_bsp = invoker.build_system_prompt
        captured: dict = {}

        def _spy_bsp(agent, context="", capability_summary=""):
            captured["context"] = context
            return original_bsp(agent, context, capability_summary)

        invoker.build_system_prompt = _spy_bsp
        # Rebuild + stream are stubbed so no live model/checkpointer is needed.
        invoker._build_deep_agent_for = AsyncMock(return_value=MagicMock())

        async def _empty_stream(*args, **kwargs):
            return
            yield  # pragma: no cover - makes this an async generator

        with patch(f"{INVOKER_MODULE}.stream_deep_agent_events", _empty_stream):
            frames = [
                f
                async for f in invoker.resume_deep_turn(
                    approval_id=approval_id,
                    decision="approve",
                    user_id=user_id,
                    workspace_id=workspace_id,
                )
            ]

        # No error frames (the resume proceeded past all guards into the rebuild).
        assert not any(f.get("event") == "error" for f in frames), f"frames={frames}"
        # THE PROOF: the persisted context sentinel was re-injected, not "".
        assert captured.get("context") == sentinel, (
            f"resume must re-inject the persisted context_block; got {captured.get('context')!r}"
        )


@pytest.mark.skipif(not _DB_REACHABLE, reason="Postgres not reachable")
async def test_resume_threads_context_into_rebuilt_gate():
    """CF-1 chained-approval carry-forward: resume must pass the persisted ``context_block``
    into ``_build_deep_agent_for`` (which threads it into the trust_gate). Without this, a
    resumed continuation that pauses AGAIN on a second write would persist ``context_block=""``
    for that chained approval, losing the original turn's context. Spy on
    ``_build_deep_agent_for`` and assert the sentinel arrives as the ``context_block`` kwarg."""
    sentinel = "ORIG_CTX_SENTINEL"

    async with _gate_env() as (factory, user_id, workspace_id):
        approval_id = f"apr_{ULID()}"
        thread_id = f"chat_{ULID()}"
        async with factory() as db:
            db.add(
                _seed_pending_approval(
                    approval_id=approval_id,
                    user_id=user_id,
                    workspace_id=workspace_id,
                    refs={
                        "thread_id": thread_id,
                        "agent_name": "executor",
                        "tool_call_id": "call_send_1",
                        "context_block": sentinel,
                    },
                )
            )
            await db.commit()

        invoker = _make_invoker(factory=factory)

        # Patch the rebuild itself so we can inspect the kwargs the resume threads into it.
        build_spy = AsyncMock(return_value=MagicMock())
        invoker._build_deep_agent_for = build_spy

        async def _empty_stream(*args, **kwargs):
            return
            yield  # pragma: no cover - makes this an async generator

        with patch(f"{INVOKER_MODULE}.stream_deep_agent_events", _empty_stream):
            frames = [
                f
                async for f in invoker.resume_deep_turn(
                    approval_id=approval_id,
                    decision="approve",
                    user_id=user_id,
                    workspace_id=workspace_id,
                )
            ]

        assert not any(f.get("event") == "error" for f in frames), f"frames={frames}"
        build_spy.assert_awaited_once()
        # THE PROOF: the persisted context is carried into the rebuilt gate, so a chained
        # approval created during this resume would carry the original turn's context (not "").
        assert build_spy.await_args.kwargs.get("context_block") == sentinel, (
            "resume must thread the persisted context_block into _build_deep_agent_for; "
            f"got {build_spy.await_args.kwargs.get('context_block')!r}"
        )
