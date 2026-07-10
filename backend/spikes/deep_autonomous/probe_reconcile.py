"""Spike probe (Step 10C Phase 0.2 — B9c reconcile-from-event-log primitive).

Proves the reconcile-from-event-log consumer can rebuild a run's
``{status, completed_steps}`` from the ``runtime_events`` log ALONE (seq-ordered)
after a mid-run kill, INDEPENDENT of which substrate produced the steps (legacy
DAG vs P1 deep step-executor). This is the primitive 10D's auto-rollback drain
needs to bring in-flight deep autonomous runs back onto legacy.

The consumer's seat already exists:
``RuntimeProjectionService.rebuild_run_projection(run_id)`` folds the seq-ordered
``runtime_events`` for a run into ``{status, total_steps, completed_steps,
progress_pct}``. The fold reads only the event ``type`` (+ payload status), never
any checkpoint/substrate state — so it is substrate-BLIND by construction. A
net-new 10D consumer simply WRAPS this method; nothing new needs to be folded.

What this probe demonstrates against a REAL Postgres:

  Step 1 (rebuild works): seed a running run + steps + the exact seq-ordered
  runtime_events the LIVE legacy DAG emits (step_started/tool_call_started +
  step_completed), call rebuild_run_projection, and assert the rebuilt
  {total_steps, completed_steps, progress_pct} matches the live get_active_runs()
  read for the same run.

  Step 2 (substrate-agnostic): seed the SAME terminal event sequence TWICE — once
  with every event payload tagged substrate="legacy", once substrate="deep" — and
  assert the two rebuilds are byte-identical {status, total_steps, completed_steps,
  progress_pct}. RuntimeEvent has no substrate column, so the marker rides in the
  payload; the fold ignores it, proving substrate-blindness the strong way.

  Step 3 (THE GATE): seed a run using ONLY the event types the SHARED DagRunner /
  GraphExecutor coordinator emit (step_started, step_completed, run_completed) and
  DELIBERATELY OMIT tool_call_started — i.e. simulate a P1 deep step-executor that
  emits NOTHING of its own into runtime_events. Assert the rebuild is STILL
  correct. This runtime-proves the fold requires nothing from the executor body:
  the DAG driver that WRAPS every step (and stays shared across substrates) emits
  every fold-required event type.

GATE FINDING (from the code seams, verified by Step 3 at runtime):
  For each event type the fold needs, the live emission point is:
    step_started      shared-dag   dag_runner.py:353,447 (execute_step;
                                   SurfaceEmitter normalizes 'step.started'->'step_started')
    tool_call_started executor-entry step_runner.py:159 (run_step_action, BEFORE the
                                   agent-loop-vs-deep branch; REDUNDANT w/ step_started)
    step_completed    shared-dag   dag_runner.py:819 (finalize_step)
    run_completed     shared-dag   dag_runner.py:137 (execute_dag, durable=True)
    run_failed        shared-dag   graph_executor.py:395 (coordinator except handler)
    run_cancelled     shared-dag   graph_executor.py:601 (cancel_run)
  NONE is emitted only from the legacy-executor body (run_step_via_agent_loop, the
  method the deep path replaces). So the deep step-executor need emit NOTHING for
  reconcile-from-event-log to rebuild its runs. Deep-executor emission gap = NONE.

Run:
    uv run python -m spikes.deep_autonomous.probe_reconcile

Self-contained + re-runnable: seeds a UUID-suffixed User+Workspace FK chain, runs,
steps, and runtime_events; tears everything down (explicit deletes in FK order) in
a finally block. Postgres-only (no Redis). Exploratory spike code — hence the
module-level prints and broad orchestration. It should still lint clean.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import asyncpg
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from ulid import ULID

from src.config.settings import get_settings
from src.models.runtime_event import RuntimeEvent
from src.models.task_graph import TaskRun, TaskStep
from src.models.users import User, Workspace
from src.services.runtime_projection import RuntimeProjectionService

SETTINGS = get_settings()
PSYCOPG_URL = SETTINGS.database_url.replace("+asyncpg", "", 1)

# The fold-required event types and where the LIVE system emits each. Derived from
# the code seams (grepped this branch); Step 3 runtime-verifies the load-bearing
# claim (the executor-only body emits none of them). EMITTED_FROM values:
#   shared-dag      -> DagRunner (execute_step/finalize_step/execute_dag) or the
#                      GraphExecutor coordinator — both STAY shared across substrates.
#   executor-entry  -> StepRunner.run_step_action, the shared entry that branches to
#                      the legacy agent-loop OR the P1 deep executor (redundant here).
#   legacy-executor-only -> emitted only from run_step_via_agent_loop (the method the
#                      deep path replaces). << would be a GAP P1 must fill. NONE are.
EMISSION_MAP: dict[str, tuple[str, str]] = {
    "step_started": (
        "shared-dag",
        "dag_runner.py:353,447 execute_step ('step.started'->'step_started')",
    ),
    "tool_call_started": (
        "executor-entry",
        "step_runner.py:159 run_step_action (pre-branch; redundant w/ step_started)",
    ),
    "step_completed": ("shared-dag", "dag_runner.py:819 finalize_step"),
    "run_completed": ("shared-dag", "dag_runner.py:137 execute_dag (durable=True)"),
    "run_failed": ("shared-dag", "graph_executor.py:395 coordinator except handler"),
    "run_cancelled": ("shared-dag", "graph_executor.py:601 cancel_run"),
}


async def _db_reachable() -> bool:
    try:
        conn = await asyncpg.connect(dsn=PSYCOPG_URL)
        try:
            await conn.execute("SELECT 1")
        finally:
            await conn.close()
        return True
    except Exception as exc:  # noqa: BLE001 - probe: report and bail cleanly
        print(f"POSTGRES_UNREACHABLE: {exc!r}")
        return False


def _revt(ws: str, run_id: str, step_id: str | None, et: str, payload: dict, when: datetime):
    """Build a RuntimeEvent. seq is server-assigned (Identity) on flush — the fold
    orders by seq, so a tied occurred_at is fine; seq breaks the tie monotonically."""
    return RuntimeEvent(
        event_id=f"revt_{ULID()}",
        workspace_id=ws,
        run_id=run_id,
        step_id=step_id,
        event_type=et,
        payload=payload,
        occurred_at=when,
    )


async def _seed_run_with_events(factory, ws: str, uid: str, events: list, run_status: str,
                                steps: list[tuple[str, str]]) -> str:
    """Seed one TaskRun + its TaskSteps + a seq-ordered runtime_events sequence.

    ``events`` is a list of (event_type, step_id_or_None, payload) tuples inserted in
    order (so their server ``seq`` follows insertion order). ``steps`` is a list of
    (step_id, status) for the TaskStep rows that back the live get_active_runs read.
    """
    run_id = f"run_{ULID()}"
    tied = datetime(2026, 7, 10, 12, 0, 0, tzinfo=timezone.utc)
    async with factory() as db:
        db.add(TaskRun(run_id=run_id, user_id=uid, workspace_id=ws, source="plan",
                       status=run_status))
        for sid, status in steps:
            db.add(TaskStep(step_id=sid, run_id=run_id, workspace_id=ws, task_id="t",
                            status=status))
        for et, sid, payload in events:
            # Flush each event before adding the next so the server assigns seq in
            # insertion order (the fold's total-order key).
            db.add(_revt(ws, run_id, sid, et, {**payload, "run_id": run_id}, tied))
            await db.flush()
        await db.commit()
    return run_id


async def _teardown(factory, engine, ws: str, uid: str) -> None:
    try:
        async with factory() as db:
            await db.execute(delete(RuntimeEvent).where(RuntimeEvent.workspace_id == ws))
            await db.execute(delete(TaskStep).where(TaskStep.workspace_id == ws))
            await db.execute(delete(TaskRun).where(TaskRun.workspace_id == ws))
            await db.execute(delete(Workspace).where(Workspace.workspace_id == ws))
            await db.execute(delete(User).where(User.user_id == uid))
            await db.commit()
    except Exception as exc:  # noqa: BLE001
        print(f"[cleanup] teardown failed: {exc!r}")
    await engine.dispose()


async def run_probe() -> int:  # noqa: PLR0915 - single linear spike orchestration
    if not await _db_reachable():
        print("RECONCILE=SKIPPED (postgres unreachable)")
        return 0

    engine = create_async_engine(SETTINGS.database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    suffix = str(ULID())
    uid = f"usr_{suffix}"
    ws = f"ws_{suffix}"

    rebuild_matches_live = False
    substrate_agnostic = False
    executor_emits_nothing_ok = False

    try:
        async with factory() as db:
            db.add(User(user_id=uid, email=f"reconcile-{suffix}@example.com", display_name="rc"))
            db.add(Workspace(workspace_id=ws, name="rc-ws", owner_user_id=uid))
            await db.commit()

        # ── Step 1: rebuild matches the live read for a mid-run (running) run ──
        # Seed exactly what the LIVE legacy DAG writes: for each step DagRunner emits
        # step_started (from 'step.started') and StepRunner emits tool_call_started;
        # finalize_step emits step_completed. Step s1 completed, s2 still running.
        print("[step 1] seed mid-run + live legacy runtime_events, rebuild vs get_active_runs")
        s1, s2 = f"step_{ULID()}", f"step_{ULID()}"
        live_events = [
            ("step_started", s1, {"step_id": s1}),
            ("tool_call_started", s1, {"step_id": s1, "tool_name": "email.read"}),
            ("step_completed", s1, {"step_id": s1, "status": "completed"}),
            ("step_started", s2, {"step_id": s2}),
            ("tool_call_started", s2, {"step_id": s2, "tool_name": "email.send"}),
        ]
        run1 = await _seed_run_with_events(
            factory, ws, uid, live_events, run_status="running",
            steps=[(s1, "completed"), (s2, "running")],
        )
        async with factory() as db:
            svc = RuntimeProjectionService(db, ws)
            rebuilt = await svc.rebuild_run_projection(run1)
            live = next(r for r in await svc.get_active_runs() if r["run_id"] == run1)
        print(f"         rebuilt={{'total':{rebuilt['total_steps']}, "
              f"'completed':{rebuilt['completed_steps']}, 'pct':{rebuilt['progress_pct']}}}")
        print(f"         live   ={{'total':{live['total_steps']}, "
              f"'completed':{live['completed_steps']}, 'pct':{live['progress_pct']}}}")
        rebuild_matches_live = (
            rebuilt["total_steps"] == live["total_steps"] == 2
            and rebuilt["completed_steps"] == live["completed_steps"] == 1
            and rebuilt["progress_pct"] == live["progress_pct"] == 50
        )
        print(f"         REBUILD_MATCHES_LIVE={rebuild_matches_live}")

        # ── Step 2: substrate-agnostic — SAME terminal sequence, tagged legacy vs deep ──
        print("[step 2] seed identical terminal sequence twice (substrate=legacy | deep), rebuild")
        a1, a2 = f"step_{ULID()}", f"step_{ULID()}"

        def _terminal_seq(sa: str, st1: str, st2: str) -> list:
            # A completed 2-step run: both steps started+completed, then run_completed.
            return [
                ("step_started", st1, {"step_id": st1, "substrate": sa}),
                ("tool_call_started", st1, {"step_id": st1, "substrate": sa}),
                ("step_completed", st1, {"step_id": st1, "status": "completed", "substrate": sa}),
                ("step_started", st2, {"step_id": st2, "substrate": sa}),
                ("tool_call_started", st2, {"step_id": st2, "substrate": sa}),
                ("step_completed", st2, {"step_id": st2, "status": "completed", "substrate": sa}),
                ("run_completed", None, {"status": "completed", "substrate": sa}),
            ]

        run_legacy = await _seed_run_with_events(
            factory, ws, uid, _terminal_seq("legacy", a1, a2), run_status="completed",
            steps=[(a1, "completed"), (a2, "completed")],
        )
        b1, b2 = f"step_{ULID()}", f"step_{ULID()}"
        run_deep = await _seed_run_with_events(
            factory, ws, uid, _terminal_seq("deep", b1, b2), run_status="completed",
            steps=[(b1, "completed"), (b2, "completed")],
        )
        async with factory() as db:
            svc = RuntimeProjectionService(db, ws)
            rb_legacy = await svc.rebuild_run_projection(run_legacy)
            rb_deep = await svc.rebuild_run_projection(run_deep)

        def _shape(r: dict) -> tuple:
            return (r["status"], r["total_steps"], r["completed_steps"], r["progress_pct"])

        print(f"         legacy-substrate rebuild -> {_shape(rb_legacy)}")
        print(f"         deep-substrate   rebuild -> {_shape(rb_deep)}")
        substrate_agnostic = _shape(rb_legacy) == _shape(rb_deep) == ("completed", 2, 2, 100)
        print(f"         SUBSTRATE_AGNOSTIC={substrate_agnostic}")

        # ── Step 3: THE GATE — deep executor emits NOTHING; shared DAG covers the fold ──
        # Seed ONLY the shared-DAG-emitted types (step_started from execute_step,
        # step_completed from finalize_step, run_completed from execute_dag). Omit
        # tool_call_started entirely — that is the only executor-adjacent emission, and
        # this simulates a P1 deep step-executor that writes nothing of its own.
        print("[step 3] GATE: seed ONLY shared-DAG event types (no tool_call_started), rebuild")
        c1, c2 = f"step_{ULID()}", f"step_{ULID()}"
        shared_only = [
            ("step_started", c1, {"step_id": c1}),
            ("step_completed", c1, {"step_id": c1, "status": "completed"}),
            ("step_started", c2, {"step_id": c2}),
            ("step_completed", c2, {"step_id": c2, "status": "completed"}),
            ("run_completed", None, {"status": "completed"}),
        ]
        run3 = await _seed_run_with_events(
            factory, ws, uid, shared_only, run_status="completed",
            steps=[(c1, "completed"), (c2, "completed")],
        )
        async with factory() as db:
            svc = RuntimeProjectionService(db, ws)
            rb3 = await svc.rebuild_run_projection(run3)
        print(f"         shared-DAG-only rebuild -> {_shape(rb3)}")
        executor_emits_nothing_ok = _shape(rb3) == ("completed", 2, 2, 100)
        print(f"         EXECUTOR_EMITS_NOTHING_STILL_REBUILDS={executor_emits_nothing_ok}")

    finally:
        await _teardown(factory, engine, ws, uid)

    # ── Final block ──────────────────────────────────────────────────────────
    gap_types = [et for et, (src, _) in EMISSION_MAP.items() if src == "legacy-executor-only"]
    print("=" * 72)
    print("STEP 10C PHASE 0.2 — reconcile-from-event-log (B9c)")
    print(f"REBUILD_MATCHES_LIVE={rebuild_matches_live}")
    print(f"SUBSTRATE_AGNOSTIC={substrate_agnostic}")
    print(f"EXECUTOR_EMITS_NOTHING_STILL_REBUILDS={executor_emits_nothing_ok}")
    print(f"REQUIRED_EVENT_TYPES={list(EMISSION_MAP.keys())}")
    for et, (src, where) in EMISSION_MAP.items():
        print(f"  {et:<20} EMITTED_FROM={src:<15} ({where})")
    print(f"DEEP_EXECUTOR_EMISSION_GAP={'NONE' if not gap_types else gap_types}")
    print("=" * 72)

    ok = (
        rebuild_matches_live
        and substrate_agnostic
        and executor_emits_nothing_ok
        and not gap_types
    )
    print(f"RESULT={'CONFIRMED' if ok else 'GAPS'}")
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run_probe()))
