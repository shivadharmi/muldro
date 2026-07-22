"""Step 10C P5: reap-on-completion for the autonomous per-step durable checkpoint thread.

``AgentInvoker.run_autonomous_deep_step`` mints a FRESH thread_id per call and NEVER resumes
it (run-level durable resume is via P4's substrate-agnostic reconcile, not per-step thread
resume). So each step's checkpoint is short-lived: minted → checkpointed under
``durability="sync"`` → reaped the moment the step finishes. These tests prove that inline
reap against real Postgres, and its dormant-safe no-op when no durable saver is wired.

1. **Reaps on completion** — with a REAL ``AsyncPostgresSaver`` and NO reap patch, after a
   ``run_autonomous_deep_step`` write completes, the thread's checkpoint rows are GONE.
2. **No-op on MemorySaver** — with ``checkpointer_provider=lambda: None`` (→ MemorySaver in
   the build) the step completes and ``reap_thread`` no-ops (returns False) — a reap no-op
   never breaks the step.
3. **ws-scoped** — the reap deletes ONLY the step's own ws-embedded thread; a second,
   unrelated thread (different ws-bound thread_id) SURVIVES (reap_thread is single-thread
   scoped by construction — this documents the isolation).

Guarded (skip when Postgres is unreachable), NullPool, seeded FK chain + checkpoint-row
teardown — all reused from the P2 harness (``test_autonomous_checkpointer``).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from langgraph.checkpoint.base import empty_checkpoint
from ulid import ULID

from src.deep_runtime.checkpoint_reaper import reap_thread as _real_reap
from src.deep_runtime.thread_identity import make_thread_id
from tests.deep_runtime.test_autonomous_checkpointer import (
    _DB_OK,
    BUILD_CHAT_MODEL,
    INVOKER_MODULE,
    WRITE_TOOL,
    _capture_thread_id,
    _checkpoint_rows,
    _db_env,
    _executor_agent,
    _FakeModel,
    _make_invoker,
    _seed_tool,
    _sqla_dsn,
    _write_tool_shell,
)

# ═══════════════ Test 1 — reaps its own thread on completion (real saver) ═══════════════


@pytest.mark.skipif(not _DB_OK, reason="Postgres not reachable")
async def test_autonomous_step_reaps_checkpoint_on_completion():
    """A ``run_autonomous_deep_step`` write, driven through a REAL injected
    ``AsyncPostgresSaver`` with NO reap patch, leaves ZERO checkpoint rows for the run's
    ws-bound ``thread_id`` — the per-step thread is reaped inline the moment the step
    finishes (durability="sync" wrote checkpoints during the step; reap-on-completion
    deleted them before the method returned)."""
    from src.deep_runtime.checkpointer import build_async_postgres_saver

    async with _db_env() as (factory, ws, uid, threads):
        await _seed_tool(
            factory, ws, name=WRITE_TOOL, capability="email.send", requires_approval=True
        )
        effects: list[str] = []
        saver = None
        pool = None
        captured: dict[str, str] = {}
        try:
            saver, pool = await build_async_postgres_saver(_sqla_dsn())
            inv = _make_invoker(factory, checkpointer=saver, effects=effects)

            with (
                patch(BUILD_CHAT_MODEL, lambda _a: _FakeModel(WRITE_TOOL, {"to": "f@x.com"})),
                patch(f"{INVOKER_MODULE}.make_thread_id", _capture_thread_id(captured)),
            ):
                out = await inv.run_autonomous_deep_step(
                    executor=_executor_agent(),
                    tools=[_write_tool_shell()],
                    message="send it",
                    context_block="",
                    user_id=uid,
                    workspace_id=ws,
                    run_id="run_reap",
                    step_id="s1",
                    pre_approved_capabilities=frozenset({"email.send"}),
                )

            assert out["status"] == "completed"
            assert effects == [WRITE_TOOL]  # the write executed once ...

            thread_id = captured["thread_id"]
            threads.add(thread_id)  # teardown safety net (rows should already be gone)

            # ... and reap-on-completion left NO checkpoint rows for the per-step thread.
            assert await _checkpoint_rows(thread_id) == 0
        finally:
            if pool is not None:
                await pool.close()


# ═══════════════ Test 2 — reap no-ops on MemorySaver (dormant-safe) ═══════════════


@pytest.mark.skipif(not _DB_OK, reason="Postgres not reachable")
async def test_autonomous_step_reap_noops_without_durable_saver():
    """With ``checkpointer_provider=lambda: None`` the deep agent builds on a MemorySaver and
    the inline reap receives ``None`` → ``reap_thread`` no-ops (returns False). The step still
    runs to a ``completed`` output dict: a reap no-op/failure NEVER breaks the step. This is the
    byte-neutral/dormant behavior when no durable saver is wired (default legacy runtime)."""
    async with _db_env() as (factory, ws, uid, threads):
        await _seed_tool(
            factory, ws, name=WRITE_TOOL, capability="email.send", requires_approval=True
        )
        effects: list[str] = []
        # checkpointer=None → self._checkpointer_provider() returns None → MemorySaver in build.
        inv = _make_invoker(factory, checkpointer=None, effects=effects)

        reap_results: list[bool] = []

        async def _spy_reap(saver, tid):
            """Wrap the REAL reaper so we can assert it was called AND observe its return."""
            result = await _real_reap(saver, tid)
            reap_results.append(result)
            return result

        with (
            patch(BUILD_CHAT_MODEL, lambda _a: _FakeModel(WRITE_TOOL, {"to": "f@x.com"})),
            patch(f"{INVOKER_MODULE}.reap_thread", _spy_reap),
        ):
            out = await inv.run_autonomous_deep_step(
                executor=_executor_agent(),
                tools=[_write_tool_shell()],
                message="send it",
                context_block="",
                user_id=uid,
                workspace_id=ws,
                run_id="run_mem",
                step_id="s1",
                pre_approved_capabilities=frozenset({"email.send"}),
            )

        assert out["status"] == "completed"  # the step is unaffected by the reap no-op
        assert effects == [WRITE_TOOL]
        # The reap fired exactly once and no-oped: provider is None → reap_thread returns False.
        assert reap_results == [False]


# ═══════════════ Test 3 — reap is single-thread-scoped (ws isolation) ═══════════════


@pytest.mark.skipif(not _DB_OK, reason="Postgres not reachable")
async def test_reap_on_completion_is_single_thread_scoped():
    """Reap-on-completion deletes ONLY the step's own ws-embedded thread. Seed a SECOND,
    unrelated thread bound to a DIFFERENT workspace directly via the saver; run the step
    (real saver, no reap patch); the step's own thread is reaped to ZERO rows while the
    unrelated second thread SURVIVES — ``reap_thread`` is single-thread-scoped by
    construction (it deletes only the exact thread_id passed to it)."""
    from src.deep_runtime.checkpointer import build_async_postgres_saver

    async with _db_env() as (factory, ws, uid, threads):
        await _seed_tool(
            factory, ws, name=WRITE_TOOL, capability="email.send", requires_approval=True
        )
        effects: list[str] = []
        saver = None
        pool = None
        captured: dict[str, str] = {}
        # A second, unrelated thread bound to a DIFFERENT (non-existent-in-DB is fine — the
        # langgraph checkpoint tables carry no workspace FK) workspace.
        other_tid = make_thread_id(f"ws_other_{ULID()}")
        threads.add(other_tid)  # teardown deletes its checkpoint rows
        try:
            saver, pool = await build_async_postgres_saver(_sqla_dsn())

            # Seed the unrelated thread's checkpoint directly (mirrors test_checkpointer.py).
            other_cfg = {"configurable": {"thread_id": other_tid, "checkpoint_ns": ""}}
            await saver.aput(
                other_cfg, empty_checkpoint(), {"source": "update", "step": 0, "parents": {}}, {}
            )
            assert await _checkpoint_rows(other_tid) >= 1

            inv = _make_invoker(factory, checkpointer=saver, effects=effects)
            with (
                patch(BUILD_CHAT_MODEL, lambda _a: _FakeModel(WRITE_TOOL, {"to": "f@x.com"})),
                patch(f"{INVOKER_MODULE}.make_thread_id", _capture_thread_id(captured)),
            ):
                out = await inv.run_autonomous_deep_step(
                    executor=_executor_agent(),
                    tools=[_write_tool_shell()],
                    message="send it",
                    context_block="",
                    user_id=uid,
                    workspace_id=ws,
                    run_id="run_scope",
                    step_id="s1",
                    pre_approved_capabilities=frozenset({"email.send"}),
                )

            assert out["status"] == "completed"
            step_tid = captured["thread_id"]
            threads.add(step_tid)

            # The step's OWN thread was reaped ...
            assert await _checkpoint_rows(step_tid) == 0
            # ... but the unrelated second thread SURVIVES (single-thread-scoped reap).
            assert await _checkpoint_rows(other_tid) >= 1
        finally:
            if pool is not None:
                await pool.close()
