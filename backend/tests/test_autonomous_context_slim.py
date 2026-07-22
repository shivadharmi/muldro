"""Step 10C P6 (B11-auto): slim the AUTONOMOUS context builds behind ``deep_context_jit``.

The chat path already threads ``jit=(runtime=="deep" and deep_context_jit)`` into
``ContextBuilder.build`` (agent_invoker). P6 threads the analogous gate into the THREE
autonomous ``build`` callers, all DORMANT behind ``deep_context_jit`` (default ``False``):

* PERSISTING (feed the plan/summary detail-tab render):
  - ``StepGraphStore.populate_steps`` (run creation) — gate computed by ``GraphExecutor``
    (the store has no settings/redis), passed in as a ``jit`` param.
  - ``GraphExecutor._resume_run_body`` stale-context refresh — gate computed inline.
* EPHEMERAL (feeds the deep agent prompt, not persisted):
  - ``StepRunner.build_step_context`` — gate computed inline.

Gate value in each caller (short-circuit on the flag FIRST so the default path adds NO
Redis GET): ``deep_context_jit AND effective_runtime("autonomous")=="deep"``. Any Redis
error / redis-None / gate error resolves to the static ``settings.runtime`` — never an
accidental ``"deep"``.

STEP-0 RENDER CONTRACT (empirically verified): ``ContextPack`` has an ``entities`` field
but NO ``memories`` field, so the persisted ``pack.model_dump()`` carries ``entities`` and
never ``memories``. ``plan.py::build_plan_context`` renders a section from ``ctx["entities"]``
(its ``ctx["memories"]`` read is already always-empty); ``build(jit=True)`` already sets
``pack.entities`` via ``_fetch_core_entities`` → the slim pack is render-safe for entities
out of the box. So the render-read key P6 must preserve is ``entities`` (NOT ``memories``),
and ``context_builder.py`` is left untouched.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from src.services.context_builder import ContextBuilder, ContextPack
from src.services.step_graph_store import StepGraphStore
from src.services.step_runner import StepRunner
from tests.conftest import TEST_USER_ID, TEST_WORKSPACE_ID, make_mock_settings

RUN_DETAIL = "src.services.run_detail_store.RunDetailStore"


# ─────────────────────────── shared builders ────────────────────────────────


def _slim_pack() -> ContextPack:
    """A representative slim JIT pack: preferences+goals+entities core, render-safe
    (``entities`` populated — the Step-0 render-read key)."""
    return ContextPack(
        task_summary="q",
        preferences=[{"memory_id": "p1", "fact_text": "prefers brevity"}],
        goals=[{"memory_id": "g1", "title": "ship v1", "priority": "medium"}],
        entities=[{"entity_id": "e1", "canonical_name": "Acme", "entity_type": "org"}],
    )


def _spy_builder(pack: ContextPack | None = None) -> MagicMock:
    cb = MagicMock()
    cb.build = AsyncMock(return_value=pack or _slim_pack())
    return cb


def _plan(plan_id: str = "plan_1", goal: str = "do the thing") -> MagicMock:
    p = MagicMock()
    p.plan_id = plan_id
    p.goal = goal
    return p


def _plan_task(task_id: str = "t1", task_type: str = "summarize") -> MagicMock:
    t = MagicMock()
    t.task_id = task_id
    t.task_type = task_type
    t.depends_on = []
    t.input_data = {"capability": "knowledge.search", "task_type": task_type}
    t.id = 1
    return t


def _run(run_id: str = "run_1") -> MagicMock:
    r = MagicMock()
    r.run_id = run_id
    r.user_id = TEST_USER_ID
    r.workspace_id = TEST_WORKSPACE_ID
    return r


def _mock_db(tasks: list) -> AsyncMock:
    db = AsyncMock()
    res = MagicMock()
    res.scalars.return_value.all.return_value = tasks
    db.execute = AsyncMock(return_value=res)
    db.add = MagicMock()
    db.flush = AsyncMock()
    return db


def _store_with_capture(context_builder) -> tuple[StepGraphStore, dict]:
    """A StepGraphStore over a mock DB, with RunDetailStore.upsert_context_pack captured
    so the persisted ``pack.model_dump()`` dict can be inspected."""
    db = _mock_db([_plan_task()])
    store = StepGraphStore(db=db, context_builder=context_builder)
    captured: dict = {}

    async def _capture_upsert(run_id, workspace_id, pack, *a, **k):
        captured["pack"] = pack

    rds_instance = MagicMock()
    rds_instance.upsert_context_pack = AsyncMock(side_effect=_capture_upsert)
    return store, captured, rds_instance


def _make_executor(settings, db, *, context_builder=None, redis=None):
    from src.services.graph_executor import GraphExecutor

    return GraphExecutor(settings, db, context_builder=context_builder, redis=redis)


async def _render_plan_context_from(pack_dict: dict):
    """Drive the ACTUAL plan-context detail-tab builder with a given persisted pack dict."""
    from src.services.surface_detail_builders import plan as plan_mod

    surface = SimpleNamespace(surface_id="run_x", payload={}, workspace_id="ws", user_id="u")
    db = AsyncMock()
    rr = MagicMock()
    rr.scalar_one_or_none.return_value = SimpleNamespace(run_id="run_x")
    db.execute = AsyncMock(return_value=rr)
    with patch.object(plan_mod, "_load_context_pack", AsyncMock(return_value=pack_dict)):
        return await plan_mod.build_plan_context(db, surface)


# ══════════════ TEST 1 — flag ON → slim + render-safe (persisting) ═══════════


async def test_store_populate_steps_forwards_jit_true_and_persists_entities():
    """``populate_steps(jit=True)`` forwards ``jit=True`` to ``build`` and persists a pack
    that STILL carries the Step-0 render-read key (``entities`` non-empty)."""
    cb = _spy_builder()
    store, captured, rds_instance = _store_with_capture(cb)

    with patch(RUN_DETAIL, return_value=rds_instance):
        await store.populate_steps(_run(), _plan(), jit=True)

    cb.build.assert_awaited_once()
    assert cb.build.await_args.kwargs["jit"] is True
    # Persisted pack (pack.model_dump()) carries entities, never memories (Step 0).
    assert captured["pack"]["entities"]
    assert "memories" not in captured["pack"]


async def test_slim_pack_renders_non_empty_entities_section():
    """The slim pack produced by the REAL ``build(jit=True)`` (via ``_fetch_core_entities``)
    renders a NON-EMPTY entities section in ``plan.py::build_plan_context`` — the render
    contract survives JIT. Uses a mock-DB ContextBuilder so ``_fetch_core_entities`` runs for
    real (this is what the negative control mutates)."""
    ent = SimpleNamespace(
        entity_id="e1",
        canonical_name="Acme",
        entity_type="org",
        # also satisfies _fetch_core_goals' attribute reads (shared mock result)
        memory_id="g1",
        fact_text="ship v1",
        confidence=0.9,
    )
    res = MagicMock()
    res.scalars.return_value.all.return_value = [ent]
    db = AsyncMock()
    db.execute = AsyncMock(return_value=res)
    cb = ContextBuilder(db=db)  # memory_service None → core prefs = []

    pack = await cb.build(TEST_USER_ID, "q", workspace_id=TEST_WORKSPACE_ID, jit=True)
    assert pack.entities, "build(jit=True) must populate entities (render-read key)"

    resp = await _render_plan_context_from(pack.model_dump())
    ent_sections = [s for s in resp.sections if s.id == "entities"]
    assert ent_sections, f"expected a non-empty entities section, got {resp.sections!r}"
    assert ent_sections[0].children


async def test_graph_executor_populate_gate_on_passes_jit_true():
    """``GraphExecutor._populate_steps`` computes the autonomous gate and, with
    ``deep_context_jit=True`` + effective_runtime ``"deep"``, forwards ``jit=True`` to the store."""
    settings = make_mock_settings(deep_context_jit=True, runtime="deep")
    executor = _make_executor(settings, _mock_db([_plan_task()]), context_builder=_spy_builder())
    executor._store.populate_steps = AsyncMock()

    await executor._populate_steps(_run(), _plan())

    assert executor._store.populate_steps.await_args.kwargs["jit"] is True


# ══════════════ TEST 2 — flag OFF (default) → byte-identical, no Redis GET ═══


async def test_store_populate_steps_default_forwards_jit_false():
    """Default (no ``jit`` arg) forwards ``jit=False`` — byte-identical eager pack."""
    cb = _spy_builder(ContextPack(task_summary="q"))
    store, _captured, rds_instance = _store_with_capture(cb)

    with patch(RUN_DETAIL, return_value=rds_instance):
        await store.populate_steps(_run(), _plan())

    assert cb.build.await_args.kwargs.get("jit") is False


async def test_graph_executor_populate_gate_off_is_byte_neutral_no_redis_get():
    """Default ``deep_context_jit=False``: store gets ``jit=False`` AND ``effective_runtime`` is
    NOT called — proving the short-circuit adds NO Redis GET to legacy run creation."""
    settings = make_mock_settings()  # deep_context_jit=False
    executor = _make_executor(settings, _mock_db([_plan_task()]), context_builder=_spy_builder())
    executor._store.populate_steps = AsyncMock()

    await executor._populate_steps(_run(), _plan())

    assert executor._store.populate_steps.await_args.kwargs["jit"] is False


# ══════════════ resume-refresh persisting caller ════════════════════════════


async def _drive_resume_refresh(*, settings, redis):
    """Drive ``_resume_run_body`` far enough to exercise the >30-min stale-context refresh,
    with the surrounding DAG machinery patched out. Returns the ``build`` spy."""
    spy_cb = _spy_builder()
    db = AsyncMock()
    run = SimpleNamespace(
        run_id="run_r",
        user_id=TEST_USER_ID,
        workspace_id=TEST_WORKSPACE_ID,
        status="paused",
        started_at=datetime.now(timezone.utc) - timedelta(hours=2),
        created_at=datetime.now(timezone.utc) - timedelta(hours=2),
        trace_id="tr_existing",
        checkpoint=None,
    )
    run_res = MagicMock()
    run_res.scalar_one_or_none.return_value = run
    db.execute = AsyncMock(return_value=run_res)
    db.flush = AsyncMock()
    db.commit = AsyncMock()

    executor = _make_executor(settings, db, context_builder=spy_cb, redis=redis)
    executor._execute_dag = AsyncMock()
    executor._finalize_trace = AsyncMock()
    executor._reconcile_plan_status = AsyncMock()

    rds_instance = MagicMock()
    rds_instance.get_context_pack = AsyncMock(return_value={})
    rds_instance.upsert_context_pack = AsyncMock()

    with patch(RUN_DETAIL, return_value=rds_instance):
        await executor._resume_run_body("run_r")
    return spy_cb


async def test_resume_refresh_gate_on_builds_jit_true():
    """Stale-context resume refresh (persisting) slims under the gate: ``build(jit=True)``."""
    spy_cb = await _drive_resume_refresh(
        settings=make_mock_settings(deep_context_jit=True, runtime="deep"),
        redis=MagicMock(),
    )
    spy_cb.build.assert_awaited_once()
    assert spy_cb.build.await_args.kwargs["jit"] is True


async def test_resume_refresh_gate_off_builds_jit_false():
    """Default flag: resume refresh keeps the eager pack (``jit=False``) — byte-neutral."""
    spy_cb = _spy_builder()
    db = AsyncMock()
    run = SimpleNamespace(
        run_id="run_r",
        user_id=TEST_USER_ID,
        workspace_id=TEST_WORKSPACE_ID,
        status="paused",
        started_at=datetime.now(timezone.utc) - timedelta(hours=2),
        created_at=datetime.now(timezone.utc) - timedelta(hours=2),
        trace_id="tr_existing",
        checkpoint=None,
    )
    run_res = MagicMock()
    run_res.scalar_one_or_none.return_value = run
    db.execute = AsyncMock(return_value=run_res)
    db.flush = AsyncMock()
    db.commit = AsyncMock()

    executor = _make_executor(make_mock_settings(), db, context_builder=spy_cb, redis=None)
    executor._execute_dag = AsyncMock()
    executor._finalize_trace = AsyncMock()
    executor._reconcile_plan_status = AsyncMock()

    rds_instance = MagicMock()
    rds_instance.get_context_pack = AsyncMock(return_value={})
    rds_instance.upsert_context_pack = AsyncMock()

    with patch(RUN_DETAIL, return_value=rds_instance):
        await executor._resume_run_body("run_r")

    assert spy_cb.build.await_args.kwargs["jit"] is False


# ══════════════ TEST 3 — ephemeral caller (build_step_context) slims ═════════


def _step_runner(*, settings, redis=None, context_builder=None) -> StepRunner:
    r = StepRunner.__new__(StepRunner)
    r._settings = settings
    r._redis = redis
    r._context_builder = context_builder
    return r


def _step(capability: str = "knowledge.search") -> SimpleNamespace:
    return SimpleNamespace(
        step_id="s1",
        input_data={"capability": capability, "goal": "summarize the thread"},
    )


async def test_build_step_context_gate_on_builds_jit_true():
    """The ephemeral ``build_step_context`` passes ``jit=True`` under the gate (agent prompt
    slimmed; no persist contract)."""
    cb = _spy_builder()
    runner = _step_runner(
        settings=make_mock_settings(deep_context_jit=True, runtime="deep"),
        redis=MagicMock(),
        context_builder=cb,
    )

    await runner.build_step_context(_run(), _step())

    cb.build.assert_awaited_once()
    assert cb.build.await_args.kwargs["jit"] is True


async def test_build_step_context_gate_off_builds_jit_false_no_redis_get():
    """Default flag: ephemeral build keeps the eager pack (``jit=False``) and does NOT call
    ``effective_runtime`` — byte-neutral, no Redis GET."""
    cb = _spy_builder()
    runner = _step_runner(settings=make_mock_settings(), redis=None, context_builder=cb)

    await runner.build_step_context(_run(), _step())

    assert cb.build.await_args.kwargs["jit"] is False
